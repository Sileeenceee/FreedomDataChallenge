"""Loading dialogues: organisers' temporary API, uploaded JSON files, and audio link resolution."""

import html
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.db import Conversation

SPEAKERS = {"client", "operator", "bot", "system"}
_SPEAKER_ALIASES = {"agent": "operator", "customer": "client", "user": "client", "assistant": "bot"}
_USER_AGENT = "cqa-hackathon-backend/0.2"
_RECORDING_RE = re.compile(
    r"(запись и транскриб|жазу және транскрип|recording and transcription)", re.IGNORECASE
)


def _http_get(url: str, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL comes from config/data
        return resp.read()


def _with_group(url: str, group_id: str) -> str:
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    query["group_id"] = group_id
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query, safe="/%")))


def fetch_group(group_id: str) -> dict:
    if not settings.cqa_api_url:
        raise ValueError("CQA_API_URL is not configured (.env)")
    payload = json.loads(_http_get(_with_group(settings.cqa_api_url, group_id)))
    if not payload.get("isSuccess", True):
        raise ValueError(f"Organisers' API error: {payload.get('error') or 'isSuccess=false'}")
    return payload


def _offset_seconds(ts: str, first: datetime | None) -> float | None:
    """Chats: 'MM:SS' (or HH:MM:SS) from start. Calls: ISO 8601 -> seconds since the first turn."""
    if not ts:
        return None
    if re.fullmatch(r"\d{1,3}:\d{2}(:\d{2})?", ts):
        parts = [int(p) for p in ts.split(":")]
        return (
            float(parts[0] * 60 + parts[1])
            if len(parts) == 2
            else float(parts[0] * 3600 + parts[1] * 60 + parts[2])
        )
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, round((dt - first).total_seconds(), 2)) if first else 0.0


def _parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def normalize_transcript(raw) -> list[dict]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    turns = []
    iso = [d for d in (_parse_iso(t.get("timestamp", "")) for t in raw) if d]
    first = min(iso) if iso else None
    # Calls: the audio starts at the "recording started" system event (checked against the MP3 length),
    # not at the first event (e.g. "agent joined"). Turns before it clamp to 0.
    for t in raw:
        if str(t.get("speaker", "")).lower() == "system" and _RECORDING_RE.search(str(t.get("text", ""))):
            first = _parse_iso(str(t.get("timestamp", ""))) or first
            break
    for i, t in enumerate(raw):
        speaker = str(t.get("speaker", "system")).strip().lower()
        speaker = _SPEAKER_ALIASES.get(speaker, speaker)
        ts = str(t.get("timestamp", "") or "")
        turns.append(
            {
                "idx": i,
                "timestamp": ts,
                "offset_s": _offset_seconds(ts, first),
                "speaker": speaker if speaker in SPEAKERS else "system",
                "text": str(t.get("text", "")),
            }
        )
    return turns


def extract_records(payload) -> tuple[list[dict], str | None]:
    """Accepts the API response, {"records": [...]}, a list of records, or one record."""
    group = None
    if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], dict):
        group = payload["data"].get("dataset_group_id")
        payload = payload["data"]
    if isinstance(payload, dict) and "records" in payload:
        group = group or payload.get("dataset_group_id")
        payload = payload["records"]
    if isinstance(payload, dict) and "transcript" in payload:
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("Unrecognised format: expected records with conversation_id and transcript")
    return payload, group


def upsert_records(session: Session, records: list[dict], group: str) -> dict:
    created = updated = 0
    for r in records:
        cid = str(r.get("conversation_id") or "").strip()
        if not cid:
            raise ValueError("Record without conversation_id")
        conv = session.get(Conversation, cid)
        fields = {
            "dataset_group": group,
            "channel": r.get("channel") or "agent chat",
            "language": (r.get("language") or "RU").upper(),
            "audio_url": r.get("audio_url") or None,
            "transcript": normalize_transcript(r.get("transcript") or []),
        }
        if conv is None:
            session.add(Conversation(id=cid, **fields))
            created += 1
        else:
            for k, v in fields.items():
                setattr(conv, k, v)
            updated += 1
    session.commit()
    return {"created": created, "updated": updated, "total": len(records), "group": group}


# --- audio -----------------------------------------------------------------------------------------
# audio_url points to an HTML player page whose <source> holds a short-lived (≈1h) signed MP3 link.
# We resolve it on demand so our own <audio> element can seek to the evidence timestamp.
_audio_cache: dict[str, tuple[float, str]] = {}
_SOURCE_RE = re.compile(r"<source[^>]+src=\"([^\"]+)\"", re.IGNORECASE)


def resolve_audio(audio_url: str) -> str:
    cached = _audio_cache.get(audio_url)
    if cached and cached[0] > time.time():
        return cached[1]
    body = _http_get(audio_url, timeout=30)
    if body[:4] == b"ID3\x03" or body[:2] == b"\xff\xfb":  # already an MP3
        return audio_url
    m = _SOURCE_RE.search(body.decode("utf-8", errors="replace"))
    if not m:
        raise ValueError("Audio source not found on the player page")
    src = html.unescape(m.group(1))
    _audio_cache[audio_url] = (time.time() + 45 * 60, src)
    return src
