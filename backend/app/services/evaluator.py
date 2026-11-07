"""Checklist evaluation of one dialogue.

`anthropic` - Claude with structured outputs (JSON schema generated from the checklist, so a new or changed
criterion needs no code change). `mock` - deterministic heuristic for offline UI work; clearly labelled.
"""

import hashlib
import json
import re
import time
from dataclasses import dataclass, field

from app.checklist import ChecklistConfig, Criterion
from app.config import settings

SPEAKER_LABEL = {"operator": "OPERATOR", "client": "CLIENT", "bot": "BOT", "system": "SYSTEM"}

SYSTEM_PROMPT = """You are a senior quality-assurance analyst in the contact centre of Freedom Broker (a securities broker in Kazakhstan). You evaluate the work of the HUMAN OPERATOR in one customer dialogue against a configurable checklist and must justify every verdict with evidence from the transcript.

How to read the transcript
- Each line is `[turn] timestamp SPEAKER: text`. Turn numbers are the only way to reference messages.
- Speakers: OPERATOR (the human agent being evaluated), CLIENT, BOT (the AI assistant AIM), SYSTEM (technical events: escalation, identity verified, call ended, ...).
- BOT and SYSTEM turns are context only. Never credit or blame the operator for what the bot did. A greeting by the bot is not the operator's greeting. A SYSTEM event such as "identity verified" counts as verification performed by the operator's process.
- Evaluate only the part of the dialogue after the operator joined, but use earlier turns to understand the client's request.
- Chat timestamps are MM:SS from the start; call timestamps are ISO 8601. You may use them to judge waiting times.
- Dialogues are in Russian, Kazakh or English. Quote evidence in the original language.
- Voice-call transcripts are automatic speech recognition output: expect misrecognised words (e.g. "федеброкер" = Freedom Broker) and judge by meaning, not spelling. Masked data like "[Имя скрыто]" is anonymisation, not an operator error.

How to evaluate each criterion
- Apply the criterion description literally; it defines what to check and how to choose the score.
- Binary criteria: 1 = requirement met, 0 = not met. Scale criteria: an integer within the stated range, following the level labels.
- N/A: only if the criterion allows N/A and its N/A rule matches this dialogue. Then set applicable=false and explain in na_reason which condition holds. If N/A is not allowed you must give a score.
- Gross error (critical criterion): if the critical rule is violated, give the minimum score.
- evidence: the turn(s) that prove your verdict, each with a short exact quote copied from that turn (max ~25 words). For a violation, cite the turn where it happened.
- If the required action is absent, evidence may be empty; then set checked_from_turn/checked_to_turn to the range of turns you examined to conclude it is missing. Otherwise set both to the first and last cited turn.
- explanation: 1-3 concrete sentences in Russian explaining why this score was given (reference turns like #12).
- confidence: "low" when the transcript is ambiguous or garbled and a human should double-check.
- summary: 1-2 sentences in Russian - what the client wanted and how it ended.
Be strict but fair, and consistent across dialogues."""


@dataclass
class EvalOutput:
    summary: str
    items: list[dict]
    model: str
    evaluator: str
    usage: dict = field(default_factory=dict)
    duration_s: float = 0.0


def render_transcript(turns: list[dict]) -> str:
    return "\n".join(
        f"[{t['idx']}] {t.get('timestamp', '')} {SPEAKER_LABEL.get(t['speaker'], t['speaker'].upper())}: {t['text']}"
        for t in turns
    )


def render_checklist(config: ChecklistConfig) -> str:
    sections = {s.id: s.title for s in config.sections}
    lines = [f"Checklist: {config.name}", config.description, ""]
    for c in config.criteria:
        lines.append(f"### criterion_id: {c.id}")
        lines.append(f"Title: {c.title}")
        if c.section_id:
            lines.append(f"Section: {sections.get(c.section_id, c.section_id)}")
        if c.type == "binary":
            lines.append("Type: binary (0 = not met, 1 = met)")
        else:
            labels = ", ".join(f"{k} = {v}" for k, v in sorted(c.scale.labels.items(), key=lambda kv: kv[0]))
            lines.append(
                f"Type: scale, integer {c.scale.min}..{c.scale.max}" + (f" ({labels})" if labels else "")
            )
        lines.append(f"What to check: {c.description or c.title}")
        lines.append(
            f"N/A allowed: {'yes - ' + (c.na_rule or 'when the criterion does not apply') if c.na_allowed else 'no'}"
        )
        if c.critical:
            lines.append(
                f"Critical (gross error): yes - {c.critical_rule or 'failing this criterion is a gross error'}"
            )
        lines.append("")
    return "\n".join(lines)


def output_schema(config: ChecklistConfig) -> dict:
    ids = [c.id for c in config.criteria]
    item = {
        "type": "object",
        "properties": {
            "criterion_id": {"type": "string", "enum": ids},
            "applicable": {"type": "boolean"},
            "na_reason": {"type": "string"},
            "value": {"type": "integer"},
            "explanation": {"type": "string"},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"turn": {"type": "integer"}, "quote": {"type": "string"}},
                    "required": ["turn", "quote"],
                    "additionalProperties": False,
                },
            },
            "checked_from_turn": {"type": "integer"},
            "checked_to_turn": {"type": "integer"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": [
            "criterion_id",
            "applicable",
            "na_reason",
            "value",
            "explanation",
            "evidence",
            "checked_from_turn",
            "checked_to_turn",
            "confidence",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"summary": {"type": "string"}, "criteria": {"type": "array", "items": item}},
        "required": ["summary", "criteria"],
        "additionalProperties": False,
    }


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


def sanitize_items(config: ChecklistConfig, turns: list[dict], raw_items: list[dict]) -> list[dict]:
    """Validate model output against the checklist and transcript; never trust indices blindly."""
    n = len(turns)
    by_id: dict[str, dict] = {}
    for r in raw_items:
        by_id.setdefault(r.get("criterion_id"), r)
    items = []
    for c in config.criteria:
        r = by_id.get(c.id)
        warnings: list[str] = []
        if r is None:
            items.append(_missing_item(c))
            continue
        applicable = bool(r.get("applicable", True))
        if not applicable and not c.na_allowed:
            warnings.append("Модель поставила N/A там, где N/A не допускается — выставлен минимальный балл.")
            applicable = True
            r = {**r, "value": c.scale.min}
        value = r.get("value")
        if applicable:
            value = c.scale.min if value is None else int(value)
            if not c.scale.min <= value <= c.scale.max:
                warnings.append(f"Значение {value} вне шкалы {c.scale.min}..{c.scale.max} — ограничено.")
                value = max(c.scale.min, min(c.scale.max, value))
        evidence = []
        for ev in r.get("evidence") or []:
            t = ev.get("turn")
            if not isinstance(t, int) or not 0 <= t < n:
                warnings.append(f"Ссылка на несуществующую реплику #{t} удалена.")
                continue
            quote = str(ev.get("quote", "")).strip()
            verified = bool(quote) and _norm(quote)[:40] in _norm(turns[t]["text"])
            evidence.append({"turn": t, "quote": quote, "verified": verified, "speaker": turns[t]["speaker"]})
        lo, hi = r.get("checked_from_turn"), r.get("checked_to_turn")
        checked = None
        if isinstance(lo, int) and isinstance(hi, int) and n:
            lo, hi = max(0, min(lo, hi)), min(n - 1, max(lo, hi))
            checked = [lo, hi]
        items.append(
            {
                "criterion_id": c.id,
                "applicable": applicable,
                "value": value if applicable else None,
                "na_reason": (r.get("na_reason") or "").strip() if not applicable else "",
                "explanation": (r.get("explanation") or "").strip(),
                "evidence": evidence,
                "checked_range": checked,
                "confidence": r.get("confidence", "medium"),
                # Optional: how the operator should have answered (shown in the UI under "Как следовало ответить").
                "suggested_reply": (r.get("suggested_reply") or "").strip(),
                "warnings": warnings,
            }
        )
    return items


def _missing_item(c: Criterion) -> dict:
    return {
        "criterion_id": c.id,
        "applicable": c.na_allowed is False,
        "value": c.scale.min if not c.na_allowed else None,
        "na_reason": "" if not c.na_allowed else "Модель не вернула результат по критерию.",
        "explanation": "Модель не вернула результат по критерию — требуется ручная проверка.",
        "evidence": [],
        "checked_range": None,
        "confidence": "low",
        "warnings": ["Нет ответа модели по критерию."],
    }


def _cost(usage: dict) -> float:
    return round(
        (
            usage.get("input_tokens", 0) * settings.price_input
            + usage.get("output_tokens", 0) * settings.price_output
            + usage.get("cache_creation_input_tokens", 0) * settings.price_cache_write
            + usage.get("cache_read_input_tokens", 0) * settings.price_cache_read
        )
        / 1_000_000,
        5,
    )


# --- Claude ---------------------------------------------------------------------------------------

_client = None


def _anthropic_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None, max_retries=4)
    return _client


def evaluate_with_claude(config: ChecklistConfig, turns: list[dict], meta: dict) -> EvalOutput:
    started = time.perf_counter()
    client = _anthropic_client()
    # Stable prefix (instructions + checklist) is cached across all dialogues of a batch.
    system = [
        {"type": "text", "text": SYSTEM_PROMPT},
        {
            "type": "text",
            "text": "CHECKLIST\n\n" + render_checklist(config),
            "cache_control": {"type": "ephemeral"},
        },
    ]
    user = (
        f"Dialogue {meta['id']} | channel: {meta['channel']} | language: {meta['language']} | turns: {len(turns)}\n\n"
        f"TRANSCRIPT\n{render_transcript(turns)}\n\n"
        "Evaluate the operator against every criterion of the checklist (exactly one result per criterion_id)."
    )
    response = client.beta.messages.create(
        model=settings.cqa_model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={
            "effort": settings.cqa_effort,
            "format": {"type": "json_schema", "schema": output_schema(config)},
        },
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # re-run on Anthropic's recommended model if a safety classifier declines
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Model declined the request ({getattr(response.stop_details, 'category', None)})")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Model output was truncated (max_tokens)")
    text = next(b.text for b in response.content if b.type == "text")
    data = json.loads(text)
    u = response.usage
    usage = {
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cache_creation_input_tokens": u.cache_creation_input_tokens or 0,
        "cache_read_input_tokens": u.cache_read_input_tokens or 0,
    }
    usage["cost_usd"] = _cost(usage)
    return EvalOutput(
        summary=data.get("summary", ""),
        items=sanitize_items(config, turns, data.get("criteria", [])),
        model=response.model,
        evaluator="anthropic",
        usage=usage,
        duration_s=round(time.perf_counter() - started, 2),
    )


# --- Mock (offline) -------------------------------------------------------------------------------

_GREET = re.compile(
    r"(здравств|добр(ый|ое)|привет|сәлем|қайырлы|hello|hi\b|good (morning|afternoon|evening))", re.I
)
_BYE = re.compile(r"(до свидания|всего доброго|хорошего|сау бол|рақмет|bye|have a (nice|good)|thank)", re.I)


def evaluate_mock(config: ChecklistConfig, turns: list[dict], meta: dict) -> EvalOutput:
    """Deterministic placeholder so the UI works without an API key. NOT a real evaluation."""
    ops = [t for t in turns if t["speaker"] == "operator"]
    raw = []
    for c in config.criteria:
        h = int(hashlib.md5(f"{meta['id']}:{c.id}".encode()).hexdigest(), 16)
        span = [ops[0]["idx"], ops[-1]["idx"]] if ops else [0, max(0, len(turns) - 1)]
        if not ops:
            raw.append(
                {
                    "criterion_id": c.id,
                    "applicable": not c.na_allowed,
                    "na_reason": "Оператор не участвовал в диалоге.",
                    "value": c.scale.min,
                    "explanation": "[MOCK] Реплик оператора нет.",
                    "evidence": [],
                    "checked_from_turn": span[0],
                    "checked_to_turn": span[1],
                    "confidence": "low",
                }
            )
            continue
        if c.na_allowed and h % 5 == 0:
            raw.append(
                {
                    "criterion_id": c.id,
                    "applicable": False,
                    "na_reason": f"[MOCK] {c.na_rule or 'Критерий неприменим.'}",
                    "value": 0,
                    "explanation": "[MOCK] Критерий неприменим к этому диалогу.",
                    "evidence": [],
                    "checked_from_turn": span[0],
                    "checked_to_turn": span[1],
                    "confidence": "medium",
                }
            )
            continue
        if c.id in ("greeting", "1") or "привет" in c.title.lower() or "контакт" in c.title.lower():
            hit = next((t for t in ops[:2] if _GREET.search(t["text"])), None)
            value = c.scale.max if hit else c.scale.min
        elif c.id == "closing" or "заверш" in c.title.lower():
            hit = next((t for t in reversed(ops) if _BYE.search(t["text"])), None)
            value = c.scale.max if hit else c.scale.min
        else:
            hit = ops[h % len(ops)]
            value = c.scale.min + (h % (c.scale.max - c.scale.min + 1)) if h % 4 == 0 else c.scale.max
        ev = [{"turn": hit["idx"], "quote": " ".join(hit["text"].split()[:12])}] if hit else []
        raw.append(
            {
                "criterion_id": c.id,
                "applicable": True,
                "na_reason": "",
                "value": value,
                "explanation": f"[MOCK] Эвристическая оценка без ИИ: {'найдено подтверждение в #' + str(hit['idx']) if hit else 'действие не найдено в репликах оператора'}.",
                "evidence": ev,
                "checked_from_turn": span[0],
                "checked_to_turn": span[1],
                "confidence": "low",
            }
        )
    first_client = next((t["text"] for t in turns if t["speaker"] == "client"), "")
    return EvalOutput(
        summary=f"[MOCK] Запрос клиента: «{first_client[:140]}»",
        items=sanitize_items(config, turns, raw),
        model="mock-heuristic",
        evaluator="mock",
        usage={"cost_usd": 0.0},
        duration_s=0.0,
    )


def evaluate_with_qwen(config: ChecklistConfig, turns: list[dict], meta: dict) -> EvalOutput:
    from app.services import qwen

    t0 = time.time()
    r = qwen.generate(qwen.build_messages(config, turns, meta))
    data = qwen.parse_json(r["text"])
    return EvalOutput(
        summary=(data.get("summary") or "").strip(),
        items=sanitize_items(config, turns, qwen.to_raw_items(config, turns, data)),
        model=settings.qwen_model,
        evaluator="qwen",
        usage={
            "input_tokens": r.get("input_tokens", 0),
            "output_tokens": r.get("output_tokens", 0),
            "gpu_seconds": r.get("seconds"),
            "cost_usd": 0.0,
            "raw_output": r["text"],
        },
        duration_s=round(time.time() - t0, 2),
    )


def evaluate(config: ChecklistConfig, turns: list[dict], meta: dict) -> EvalOutput:
    if settings.evaluator_mode == "qwen":
        return evaluate_with_qwen(config, turns, meta)
    if settings.evaluator_mode == "anthropic":
        return evaluate_with_claude(config, turns, meta)
    return evaluate_mock(config, turns, meta)
