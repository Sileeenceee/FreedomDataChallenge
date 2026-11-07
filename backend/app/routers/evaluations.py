from collections import Counter, defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.checklist import ChecklistConfig
from app.config import settings
from app.db import Appeal, Checklist, Conversation, Evaluation, get_session, now
from app.routers.checklists import active_checklist
from app.serializers import appeal_out, evaluation_full
from app.services.runner import apply_decisions, queue_evaluations

router = APIRouter(prefix="/api", tags=["evaluations"])


class EvaluateRequest(BaseModel):
    conversation_ids: list[str] | None = None
    group: str | None = None  # evaluate a whole dataset group (e.g. the control set)
    checklist_id: int | None = None  # default: active checklist
    only_missing: bool = False  # skip dialogues already evaluated with this checklist


@router.post("/evaluations", status_code=202)
def start_evaluations(req: EvaluateRequest, session: Session = Depends(get_session)):
    checklist = session.get(Checklist, req.checklist_id) if req.checklist_id else active_checklist(session)
    if checklist is None:
        raise HTTPException(400, "Checklist not found")
    if settings.evaluator_mode == "qwen" and not settings.qwen_url:
        raise HTTPException(
            409, "Qwen не подключён: запустите ячейку сервера в Colab и откройте ссылку, которую она напечатает."
        )
    q = select(Conversation.id)
    if req.conversation_ids:
        q = q.where(Conversation.id.in_(req.conversation_ids))
    elif req.group:
        q = q.where(Conversation.dataset_group == req.group)
    ids = list(session.scalars(q.order_by(Conversation.id)).all())
    if req.only_missing:
        done = set(
            session.scalars(
                select(Evaluation.conversation_id).where(
                    Evaluation.checklist_id == checklist.id,
                    Evaluation.status.in_(["done", "pending", "running"]),
                )
            ).all()
        )
        ids = [i for i in ids if i not in done]
    if not ids:
        return {"batch_id": None, "count": 0, "checklist_id": checklist.id}
    batch_id, eval_ids = queue_evaluations(session, ids, checklist)
    return {"batch_id": batch_id, "count": len(eval_ids), "checklist_id": checklist.id}


@router.get("/batches/{batch_id}")
def batch_status(batch_id: str, session: Session = Depends(get_session)):
    evs = session.scalars(select(Evaluation).where(Evaluation.batch_id == batch_id)).all()
    if not evs:
        raise HTTPException(404, "Batch not found")
    counts = Counter(e.status for e in evs)
    return {
        "batch_id": batch_id,
        "total": len(evs),
        "counts": counts,
        "finished": counts.get("done", 0) + counts.get("error", 0),
        "cost_usd": round(sum((e.usage or {}).get("cost_usd", 0) or 0 for e in evs), 4),
    }


@router.get("/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id: int, session: Session = Depends(get_session)):
    ev = session.get(Evaluation, evaluation_id)
    if ev is None:
        raise HTTPException(404, "Evaluation not found")
    return evaluation_full(ev)


# --- appeals ----------------------------------------------------------------------------------------


class AppealRequest(BaseModel):
    criterion_id: str
    comment: str = Field(min_length=3)
    author: str | None = None


@router.post("/evaluations/{evaluation_id}/appeals", status_code=201)
def create_appeal(evaluation_id: int, req: AppealRequest, session: Session = Depends(get_session)):
    ev = session.get(Evaluation, evaluation_id)
    if ev is None or ev.status != "done":
        raise HTTPException(400, "Evaluation not found or not finished")
    item = next((i for i in ev.final_items if i["criterion_id"] == req.criterion_id), None)
    if item is None:
        raise HTTPException(400, f"Unknown criterion '{req.criterion_id}'")
    if any(a.criterion_id == req.criterion_id and a.status == "open" for a in ev.appeals):
        raise HTTPException(409, "An open appeal for this criterion already exists")
    appeal = Appeal(
        evaluation_id=ev.id,
        criterion_id=req.criterion_id,
        comment=req.comment.strip(),
        author=req.author,
        original_item=item,
        total_before=(ev.final_result or {}).get("total"),
    )
    session.add(appeal)
    session.commit()
    return appeal_out(appeal)


@router.get("/appeals")
def list_appeals(status: Literal["open", "decided", "all"] = "all", session: Session = Depends(get_session)):
    q = select(Appeal).options(selectinload(Appeal.evaluation)).order_by(Appeal.id.desc())
    if status == "open":
        q = q.where(Appeal.status == "open")
    elif status == "decided":
        q = q.where(Appeal.status != "open")
    out = []
    for a in session.scalars(q).all():
        crit = next(
            (c for c in a.evaluation.checklist_snapshot.get("criteria", []) if c["id"] == a.criterion_id), {}
        )
        out.append(
            {
                **appeal_out(a),
                "conversation_id": a.evaluation.conversation_id,
                "criterion_title": crit.get("title", a.criterion_id),
                "current_total": (a.evaluation.final_result or {}).get("total"),
            }
        )
    return out


@router.get("/appeals/{appeal_id}")
def get_appeal(appeal_id: int, session: Session = Depends(get_session)):
    a = session.get(Appeal, appeal_id)
    if a is None:
        raise HTTPException(404, "Appeal not found")
    ev = a.evaluation
    conv = ev.conversation
    crit = next((c for c in ev.checklist_snapshot["criteria"] if c["id"] == a.criterion_id), None)
    item = a.original_item
    focus = {e["turn"] for e in item.get("evidence", [])}
    if item.get("checked_range"):
        lo, hi = item["checked_range"]
        focus |= set(range(lo, hi + 1))
    if not focus:
        focus = {t["idx"] for t in conv.transcript if t["speaker"] == "operator"}
    lo, hi = (max(0, min(focus) - 2), min(len(conv.transcript) - 1, max(focus) + 2)) if focus else (0, -1)
    return {
        **appeal_out(a),
        "conversation_id": conv.id,
        "channel": conv.channel,
        "language": conv.language,
        "has_audio": bool(conv.audio_url),
        "criterion": crit,
        "fragment": conv.transcript[lo : hi + 1],
        "evaluation": evaluation_full(ev),
    }


class DecisionRequest(BaseModel):
    decision: Literal["confirm", "change"]
    reason: str = Field(min_length=3)
    applicable: bool = True
    value: int | None = None
    na_reason: str | None = None
    reviewer: str | None = None


@router.post("/appeals/{appeal_id}/decision")
def decide(appeal_id: int, req: DecisionRequest, session: Session = Depends(get_session)):
    a = session.get(Appeal, appeal_id)
    if a is None:
        raise HTTPException(404, "Appeal not found")
    if a.status != "open":
        raise HTTPException(409, "Appeal already decided")
    ev = a.evaluation
    config = ChecklistConfig.model_validate(ev.checklist_snapshot)
    crit = next(c for c in config.criteria if c.id == a.criterion_id)
    total_before = (ev.final_result or {}).get("total")

    if req.decision == "change":
        if not req.applicable and not crit.na_allowed:
            raise HTTPException(400, "N/A is not allowed for this criterion")
        if req.applicable and (req.value is None or not crit.scale.min <= req.value <= crit.scale.max):
            raise HTTPException(400, f"Value must be within {crit.scale.min}..{crit.scale.max}")
        a.decided_item = {
            "applicable": req.applicable,
            "value": req.value if req.applicable else None,
            "na_reason": (req.na_reason or req.reason) if not req.applicable else "",
            "overridden": True,
            "appeal_id": a.id,
            "reviewer_reason": req.reason,
        }
        a.status = "changed"
    else:
        a.decided_item = None
        a.status = "confirmed"
    a.decision_reason = req.reason.strip()
    a.reviewer = req.reviewer
    a.decided_at = now()
    a.total_before = total_before
    session.flush()
    apply_decisions(ev)
    a.total_after = ev.final_result.get("total")
    session.commit()
    return {**appeal_out(a), "evaluation": evaluation_full(ev)}


# --- stats --------------------------------------------------------------------------------------------


@router.get("/stats")
def stats(session: Session = Depends(get_session)):
    convs = session.scalars(select(Conversation).options(selectinload(Conversation.evaluations))).all()
    latest = [c.evaluations[0] for c in convs if c.evaluations]
    done = [e for e in latest if e.status == "done" and (e.final_result or {}).get("total") is not None]
    by_lang: dict[str, list[float]] = defaultdict(list)
    lang = {c.id: c.language for c in convs}
    for e in done:
        by_lang[lang[e.conversation_id]].append(e.final_result["total"])
    costs = [(e.usage or {}).get("cost_usd") for e in done if (e.usage or {}).get("cost_usd")]
    appeals = session.scalars(select(Appeal)).all()
    return {
        "conversations": len(convs),
        "evaluated": len(done),
        "in_progress": sum(1 for e in latest if e.status in ("pending", "running")),
        "errors": sum(1 for e in latest if e.status == "error"),
        "avg_total": round(sum(e.final_result["total"] for e in done) / len(done), 1) if done else None,
        "critical": sum(1 for e in done if e.final_result.get("critical_error")),
        "by_language": {k: round(sum(v) / len(v), 1) for k, v in sorted(by_lang.items())},
        "avg_cost_usd": round(sum(costs) / len(costs), 4) if costs else None,
        "appeals_open": sum(1 for a in appeals if a.status == "open"),
        "appeals_decided": sum(1 for a in appeals if a.status != "open"),
    }
