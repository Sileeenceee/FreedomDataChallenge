"""Connect the backend to the Qwen server running in Colab, at runtime (no .env edit, no restart)."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import qwen

router = APIRouter(prefix="/api/qwen", tags=["qwen"])


class ConnectRequest(BaseModel):
    url: str
    token: str = ""


@router.get("/status")
def get_status():
    return qwen.status(force=True)


@router.post("/connect")
def connect(req: ConnectRequest):
    try:
        return qwen.connect(req.url, req.token)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e)) from e


@router.post("/disconnect", status_code=204)
def disconnect():
    qwen.disconnect()
