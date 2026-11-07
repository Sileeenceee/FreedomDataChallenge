from app.db import Appeal, Checklist, Evaluation


def checklist_out(c: Checklist, full: bool = True) -> dict:
    out = {
        "id": c.id,
        "name": c.name,
        "family": c.family,
        "version": c.version,
        "is_active": c.is_active,
        "comment": c.comment,
        "created_at": c.created_at,
        "criteria_count": len(c.config.get("criteria", [])),
    }
    if full:
        out["config"] = c.config
    return out


def evaluation_brief(e: Evaluation) -> dict:
    res = e.final_result or {}
    orig = e.result or {}
    return {
        "id": e.id,
        "status": e.status,
        "error": e.error,
        "checklist_id": e.checklist_id,
        "checklist_name": (e.checklist_snapshot or {}).get("name"),
        "batch_id": e.batch_id,
        "evaluator": e.evaluator,
        "model": e.model,
        "total": res.get("total"),
        "original_total": orig.get("total"),
        "max_total": res.get("max_total"),
        "critical_error": res.get("critical_error", False),
        "low_confidence": sum(1 for i in (e.items or []) if i.get("confidence") == "low"),
        "appeals_open": sum(1 for a in e.appeals if a.status == "open"),
        "appeals_total": len(e.appeals),
        "cost_usd": (e.usage or {}).get("cost_usd"),
        "created_at": e.created_at,
        "finished_at": e.finished_at,
    }


def appeal_out(a: Appeal) -> dict:
    return {
        "id": a.id,
        "evaluation_id": a.evaluation_id,
        "criterion_id": a.criterion_id,
        "comment": a.comment,
        "author": a.author,
        "status": a.status,
        "original_item": a.original_item,
        "decision_reason": a.decision_reason,
        "decided_item": a.decided_item,
        "reviewer": a.reviewer,
        "total_before": a.total_before,
        "total_after": a.total_after,
        "created_at": a.created_at,
        "decided_at": a.decided_at,
    }


def evaluation_full(e: Evaluation) -> dict:
    return {
        **evaluation_brief(e),
        "conversation_id": e.conversation_id,
        "summary": e.summary,
        "checklist": e.checklist_snapshot,
        "items": e.items,
        "result": e.result,
        "final_items": e.final_items,
        "final_result": e.final_result,
        "usage": e.usage,
        "duration_s": e.duration_s,
        "appeals": [appeal_out(a) for a in e.appeals],
    }
