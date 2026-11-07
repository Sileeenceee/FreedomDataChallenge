"""Remote Qwen evaluator: the model runs in Google Colab (GPU) behind a tunnel, see colab/qwen_server.py.

The backend builds the prompts (system prompt from app/prompts.py + the checklist rendered from the ACTIVE
checklist config, so edits made in the UI reach the model), Colab only generates text. Jobs are async on
the Colab side (POST /jobs, then poll GET /jobs/{id}) because tunnels cut requests after ~100 s.
"""

import json
import re
import time
import urllib.error
import urllib.request

from app.checklist import ChecklistConfig, Criterion
from app.config import settings
from app.prompts import system_prompt

_YES = {"да", "yes", "true", "1", "иә", "ия"}
_NO = {"нет", "no", "false", "0", "жоқ"}
_NA = {"n/a", "na", "н/д", "-", "—", ""}


def render_dialogue(turns: list[dict]) -> str:
    # Same format as the team's qwen_dataset.json: "[0] [00:00] client: text"
    return "\n".join(f"[{t['idx']}] [{t.get('timestamp', '')}] {t['speaker']}: {t['text']}" for t in turns)


def _allowed(c: Criterion) -> list[int]:
    if c.type == "binary":
        return [0, 1]
    keys = sorted(int(k) for k in c.scale.labels if re.fullmatch(r"-?\d+", str(k)))
    return keys or list(range(c.scale.min, c.scale.max + 1))


def render_checklist(config: ChecklistConfig) -> str:
    blocks = []
    for c in config.criteria:
        if c.type == "binary":
            scores = ['"Да"', '"Нет"']
            scale = ""
        else:
            scores = [str(v) for v in _allowed(c)]
            scale = "\n".join(
                f"{k}: {v}" for k, v in sorted(c.scale.labels.items(), key=lambda kv: -int(kv[0]))
            )
        if c.na_allowed:
            scores.append('"N/A"')
        lines = [
            f"КРИТЕРИЙ {c.id} — {c.title.upper()}",
            f"Вес: {c.weight:g}",
            f"Возможные оценки: {', '.join(scores)}",
        ]
        if scale:
            lines.append(scale)
        if c.description:
            lines.append(c.description)
        lines.append(
            f'"N/A": {c.na_rule}'
            if c.na_allowed and c.na_rule
            else ("" if c.na_allowed else "N/A запрещено.")
        )
        blocks.append("\n".join(x for x in lines if x))
    return "\n\n".join(blocks)


def build_user_prompt(config: ChecklistConfig, turns: list[dict], meta: dict) -> str:
    ids = ", ".join(f'"{c.id}"' for c in config.criteria)
    return f"""Оцени следующий диалог по официальному чек-листу.

ЧЕК-ЛИСТ:
{render_checklist(config)}

ИНФОРМАЦИЯ О ДИАЛОГЕ:
conversation_id: {meta.get("id")}
language: {meta.get("language")}

ДИАЛОГ:
{render_dialogue(turns)}

ИНСТРУКЦИЯ:
Для каждого критерия ({ids}):
1. Определи применимость.
2. Выбери оценку строго из разрешённых значений.
3. Объясни решение в 1–2 предложениях.
4. Укажи номера реплик, подтверждающих решение.

Верни только JSON следующего формата:
{{
  "conversation_id": "{meta.get("id")}",
  "criteria": [
    {{"id": "{config.criteria[0].id}", "score": "Да", "explanation": "Краткое обоснование", "evidence_ids": [9]}}
  ]
}}

В массиве criteria должно быть ровно {len(config.criteria)} объектов — по одному на каждый критерий.
Не добавляй итоговый балл. Не добавляй текст за пределами JSON."""


# --- transport ------------------------------------------------------------------------------------


def _request(method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
    if not settings.qwen_url:
        raise RuntimeError("QWEN_URL не задан в .env — запустите ячейку сервера в Colab и вставьте ссылку.")
    req = urllib.request.Request(
        settings.qwen_url.rstrip("/") + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", "User-Agent": "cqa-backend"},
    )
    if settings.qwen_token:
        req.add_header("Authorization", f"Bearer {settings.qwen_token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Colab вернул {e.code}: {e.read().decode(errors='replace')[:300]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Colab недоступен ({e.reason}). Проверьте, что ячейка сервера запущена и QWEN_URL актуален."
        ) from e


def generate(messages: list[dict], max_new_tokens: int = 1500) -> dict:
    job = _request("POST", "/jobs", {"messages": messages, "max_new_tokens": max_new_tokens})
    deadline = time.time() + settings.qwen_timeout
    while time.time() < deadline:
        time.sleep(3)
        r = _request("GET", f"/jobs/{job['id']}")
        if r.get("status") == "done":
            return r
        if r.get("status") == "error":
            raise RuntimeError(f"Ошибка генерации в Colab: {r.get('error')}")
    raise RuntimeError(f"Colab не ответил за {settings.qwen_timeout} с.")


def health() -> dict:
    return _request("GET", "/health", timeout=10)


# --- parsing --------------------------------------------------------------------------------------


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"Модель вернула не JSON: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def to_value(c: Criterion, score) -> int | None:
    """Model score -> checklist value; None means N/A."""
    if score is None:
        return None
    s = str(score).strip().lower()
    if s in _NA:
        return None
    if c.type == "binary":
        if s in _YES:
            return 1
        if s in _NO:
            return 0
        raise ValueError(f"оценка {score!r}")
    try:
        v = float(s.replace(",", "."))
    except ValueError as e:
        raise ValueError(f"оценка {score!r}") from e
    allowed = _allowed(c)
    return min(allowed, key=lambda a: abs(a - v))


def to_raw_items(config: ChecklistConfig, turns: list[dict], data: dict) -> list[dict]:
    """Model JSON -> the raw item format that evaluator.sanitize_items validates."""
    by_id = {str(r.get("id")).strip(): r for r in data.get("criteria") or [] if isinstance(r, dict)}
    n = len(turns)
    raw = []
    for c in config.criteria:
        r = by_id.get(c.id)
        if r is None:
            continue
        note = ""
        try:
            value = to_value(c, r.get("score"))
        except ValueError as e:
            value, note = c.scale.min, f" [Недопустимая {e} — выставлен минимум.]"
        ids = [i for i in (r.get("evidence_ids") or []) if isinstance(i, int)]
        evidence = [{"turn": i, "quote": turns[i]["text"][:200] if 0 <= i < n else ""} for i in ids]
        explanation = (r.get("explanation") or "").strip() + note
        raw.append(
            {
                "criterion_id": c.id,
                "applicable": value is not None,
                "value": value,
                "na_reason": explanation if value is None else "",
                "explanation": explanation,
                "evidence": evidence,
                "checked_from_turn": min(ids) if ids else None,
                "checked_to_turn": max(ids) if ids else None,
                "confidence": "medium",
                "suggested_reply": r.get("suggested_reply") or "",
            }
        )
    return raw


def build_messages(config: ChecklistConfig, turns: list[dict], meta: dict) -> list[dict]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": build_user_prompt(config, turns, meta)},
    ]
