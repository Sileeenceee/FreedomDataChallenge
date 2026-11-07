"""Background evaluation queue (in-process thread pool) + score recomputation after appeals."""

import logging
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checklist import ChecklistConfig, compute_scores
from app.config import settings
from app.db import Checklist, Conversation, Evaluation, SessionLocal, now
from app.services.evaluator import evaluate

log = logging.getLogger("cqa.runner")
_pool = ThreadPoolExecutor(max_workers=max(1, settings.eval_concurrency), thread_name_prefix="eval")


def queue_evaluations(
    session: Session, conversation_ids: list[str], checklist: Checklist
) -> tuple[str, list[int]]:
    batch_id = uuid.uuid4().hex[:10]
    ids = []
    for cid in conversation_ids:
        ev = Evaluation(
            conversation_id=cid,
            checklist_id=checklist.id,
            checklist_snapshot=checklist.config,
            batch_id=batch_id,
            status="pending",
            evaluator=settings.evaluator_mode,
        )
        session.add(ev)
        session.flush()
        ids.append(ev.id)
    session.commit()
    for eid in ids:
        _pool.submit(_run, eid)
    return batch_id, ids


def _run(evaluation_id: int) -> None:
    with SessionLocal() as session:
        ev = session.get(Evaluation, evaluation_id)
        if ev is None or ev.status != "pending":
            return
        ev.status = "running"
        session.commit()
        try:
            config = ChecklistConfig.model_validate(ev.checklist_snapshot)
            conv = session.get(Conversation, ev.conversation_id)
            out = evaluate(
                config,
                conv.transcript,
                {"id": conv.id, "channel": conv.channel, "language": conv.language},
            )
            ev.summary = out.summary
            ev.items = out.items
            ev.result = compute_scores(config, out.items)
            ev.final_items = out.items
            ev.final_result = ev.result
            ev.model = out.model
            ev.evaluator = out.evaluator
            ev.usage = out.usage
            ev.duration_s = out.duration_s
            ev.status = "done"
        except Exception as e:  # keep the worker alive; surface the error in the UI
            log.error("Evaluation %s failed: %s", evaluation_id, traceback.format_exc())
            ev.status = "error"
            ev.error = f"{type(e).__name__}: {e}"[:2000]
        ev.finished_at = now()
        session.commit()


def recover_interrupted() -> None:
    """Evaluations left pending/running by a restart are re-queued."""
    with SessionLocal() as session:
        stuck = session.scalars(select(Evaluation).where(Evaluation.status.in_(["pending", "running"]))).all()
        for ev in stuck:
            ev.status = "pending"
        session.commit()
        for ev in stuck:
            _pool.submit(_run, ev.id)


def apply_decisions(ev: Evaluation) -> None:
    """Rebuild final_items from the AI items + all decided appeals (latest decision per criterion wins)."""
    config = ChecklistConfig.model_validate(ev.checklist_snapshot)
    final = {i["criterion_id"]: dict(i) for i in ev.items}
    for appeal in sorted(ev.appeals, key=lambda a: (a.decided_at or a.created_at, a.id)):
        if appeal.status == "changed" and appeal.decided_item:
            final[appeal.criterion_id] = {**final[appeal.criterion_id], **appeal.decided_item}
    ev.final_items = [final[c.id] for c in config.criteria if c.id in final]
    ev.final_result = compute_scores(config, ev.final_items)
