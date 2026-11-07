import json
from collections import Counter

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import Conversation, Evaluation, get_session
from app.serializers import evaluation_brief
from app.services import data_source

router = APIRouter(prefix="/api", tags=["conversations"])


class ImportRequest(BaseModel):
    group_id: str = "DEV"


@router.post("/import")
def import_group(req: ImportRequest, session: Session = Depends(get_session)):
    """Fetch a dataset group from the organisers' API (server-side, so no CORS issues)."""
    try:
        payload = data_source.fetch_group(req.group_id.strip())
        records, group = data_source.extract_records(payload)
        return data_source.upsert_records(session, records, group or req.group_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except OSError as e:
        raise HTTPException(502, f"Organisers' API unreachable: {e}") from e


@router.post("/upload")
def upload(
    file: UploadFile = File(...), group: str | None = Form(None), session: Session = Depends(get_session)
):
    """Upload a JSON file: API response, {records:[...]}, a list of records, or a single record."""
    try:
        payload = json.loads(file.file.read().decode("utf-8-sig"))
        records, detected = data_source.extract_records(payload)
        return data_source.upsert_records(session, records, group or detected or "UPLOAD")
    except (ValueError, UnicodeDecodeError) as e:
        raise HTTPException(400, f"Invalid file: {e}") from e


class RecordsRequest(BaseModel):
    payload: dict | list
    group: str | None = None


@router.post("/conversations")
def add_records(req: RecordsRequest, session: Session = Depends(get_session)):
    """Paste JSON (same formats as upload)."""
    try:
        records, detected = data_source.extract_records(req.payload)
        return data_source.upsert_records(session, records, req.group or detected or "UPLOAD")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/conversations")
def list_conversations(session: Session = Depends(get_session)):
    convs = session.scalars(
        select(Conversation)
        .options(selectinload(Conversation.evaluations).selectinload(Evaluation.appeals))
        .order_by(Conversation.dataset_group, Conversation.id)
    ).all()
    out = []
    for c in convs:
        latest = c.evaluations[0] if c.evaluations else None
        speakers = Counter(t["speaker"] for t in c.transcript)
        out.append(
            {
                "id": c.id,
                "group": c.dataset_group,
                "channel": c.channel,
                "language": c.language,
                "has_audio": bool(c.audio_url),
                "turns": len(c.transcript),
                "operator_turns": speakers.get("operator", 0),
                "preview": next((t["text"] for t in c.transcript if t["speaker"] == "client"), "")[:160],
                "evaluation": evaluation_brief(latest) if latest else None,
                "evaluations_count": len(c.evaluations),
            }
        )
    return out


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, session: Session = Depends(get_session)):
    c = session.get(Conversation, conversation_id)
    if c is None:
        raise HTTPException(404, "Conversation not found")
    return {
        "id": c.id,
        "group": c.dataset_group,
        "channel": c.channel,
        "language": c.language,
        "audio_url": c.audio_url,
        "transcript": c.transcript,
        "evaluations": [evaluation_brief(e) for e in c.evaluations],
    }


@router.get("/conversations/{conversation_id}/audio")
def audio(conversation_id: str, session: Session = Depends(get_session)):
    """Redirects to a fresh signed MP3 link so the UI's <audio> can seek to evidence timestamps."""
    c = session.get(Conversation, conversation_id)
    if c is None or not c.audio_url:
        raise HTTPException(404, "No recording for this conversation")
    try:
        return RedirectResponse(data_source.resolve_audio(c.audio_url), status_code=307)
    except (ValueError, OSError) as e:
        raise HTTPException(502, f"Could not resolve the recording: {e}") from e


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, session: Session = Depends(get_session)):
    c = session.get(Conversation, conversation_id)
    if c is None:
        raise HTTPException(404, "Conversation not found")
    session.delete(c)
    session.commit()
