import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.checklist import ChecklistConfig
from app.db import Checklist, get_session
from app.default_checklist import DEFAULT_CHECKLIST
from app.serializers import checklist_out

router = APIRouter(prefix="/api/checklists", tags=["checklists"])


def validate_config(raw: dict) -> ChecklistConfig:
    try:
        return ChecklistConfig.model_validate(raw)
    except ValidationError as e:
        msgs = []
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"])
            msgs.append(f"{loc}: {err['msg']}" if loc else err["msg"])
        raise HTTPException(400, "; ".join(msgs)) from e


def active_checklist(session: Session) -> Checklist | None:
    return session.scalars(select(Checklist).where(Checklist.is_active).order_by(Checklist.id.desc())).first()


def seed_default(session: Session) -> None:
    if session.scalar(select(func.count()).select_from(Checklist)):
        return
    cfg = ChecklistConfig.model_validate(DEFAULT_CHECKLIST)
    session.add(
        Checklist(
            name=cfg.name,
            family=uuid.uuid4().hex[:12],
            version=1,
            config=cfg.model_dump(),
            is_active=True,
            comment="Стартовая конфигурация",
        )
    )
    session.commit()


@router.get("")
def list_checklists(session: Session = Depends(get_session)):
    rows = session.scalars(select(Checklist).order_by(Checklist.family, Checklist.version.desc())).all()
    return [checklist_out(c, full=False) for c in rows]


@router.get("/active")
def get_active(session: Session = Depends(get_session)):
    c = active_checklist(session)
    if c is None:
        raise HTTPException(404, "No active checklist")
    return checklist_out(c)


@router.get("/{checklist_id}")
def get_checklist(checklist_id: int, session: Session = Depends(get_session)):
    c = session.get(Checklist, checklist_id)
    if c is None:
        raise HTTPException(404, "Checklist not found")
    return checklist_out(c)


class SaveRequest(BaseModel):
    config: dict
    base_id: int | None = None  # save as the next version of this checklist; None = new checklist
    comment: str | None = None
    activate: bool = True


@router.post("", status_code=201)
def save_checklist(req: SaveRequest, session: Session = Depends(get_session)):
    cfg = validate_config(req.config)
    if req.base_id is not None:
        base = session.get(Checklist, req.base_id)
        if base is None:
            raise HTTPException(404, "Base checklist not found")
        family = base.family
        version = (
            session.scalar(select(func.max(Checklist.version)).where(Checklist.family == family)) or 0
        ) + 1
    else:
        family, version = uuid.uuid4().hex[:12], 1
    if req.activate:
        session.execute(update(Checklist).values(is_active=False))
    c = Checklist(
        name=cfg.name,
        family=family,
        version=version,
        config=cfg.model_dump(),
        comment=req.comment,
        is_active=req.activate,
    )
    session.add(c)
    session.commit()
    return checklist_out(c)


@router.post("/validate")
def validate(req: SaveRequest):
    cfg = validate_config(req.config)
    return {"ok": True, "total_weight": sum(c.weight for c in cfg.criteria), "config": cfg.model_dump()}


@router.post("/{checklist_id}/activate")
def activate(checklist_id: int, session: Session = Depends(get_session)):
    c = session.get(Checklist, checklist_id)
    if c is None:
        raise HTTPException(404, "Checklist not found")
    session.execute(update(Checklist).values(is_active=False))
    c.is_active = True
    session.commit()
    return checklist_out(c)
