"""Team Qwen evaluator (CQA_evaluator_v3): the model runs in Google Colab behind a tunnel (colab/qwen_server.py).

Pipeline per dialogue, all prompt building and post-processing happens here, Colab only generates text:
  1. FACTS computed in code (first/last operator message, how the dialogue ended, system identity check, holds).
  2. Prompt = checklist (from the ACTIVE checklist config, so UI edits reach the model) + facts + dialogue.
     Weak criteria carry typed `check_fields` the model fills before explanation and score.
  3. Parse + coerce the JSON, then RULES (criterion.derive) combine fields + facts into the final score.
     A criterion without `derive` keeps the model's score.
  4. Optional voting (QWEN_RUNS=3): sampled runs, majority on the final scores; disagreement -> low confidence.
Jobs are async on the Colab side (POST /jobs, then poll GET /jobs/{id}) because tunnels cut requests after ~100 s.
"""

import collections
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

from app.checklist import ChecklistConfig, Criterion
from app.config import settings
from app.prompts import SYSTEM_PROMPT

log = logging.getLogger("cqa.qwen")

# =============================================================================================
# Connection to Colab (runtime-configurable, persisted in data/qwen_connection.json)
# =============================================================================================

_STATE_FILE = settings.data_dir / "qwen_connection.json"
_status_cache: dict = {"at": 0.0, "value": None}
_lock = threading.Lock()


class ColabUnreachable(RuntimeError):
    pass


def load_saved_connection() -> None:
    try:
        saved = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if saved.get("url"):
        settings.qwen_url, settings.qwen_token = saved["url"], saved.get("token", "")
        log.info("Qwen connection restored: %s", settings.qwen_url)


def _normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url and not re.match(r"^https?://", url):
        url = "https://" + url
    return url


def _request(method: str, path: str, body: dict | None = None, timeout: float = 30, url: str | None = None,
             token: str | None = None) -> dict:
    base = url if url is not None else settings.qwen_url
    token = settings.qwen_token if token is None else token
    if not base:
        raise ColabUnreachable("Qwen не подключён: запустите ячейку сервера в Colab и откройте ссылку, которую она напечатает.")
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", "User-Agent": "cqa-backend"},
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - URL set by the operator
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code in (502, 503, 504, 530):  # tunnel up but Colab runtime gone / restarting
            raise ColabUnreachable(f"Colab не отвечает (HTTP {e.code}).") from e
        err = RuntimeError(f"Colab вернул {e.code}: {detail}")
        err.status = e.code  # type: ignore[attr-defined]
        raise err from e
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        reason = getattr(e, "reason", e)
        raise ColabUnreachable(f"Colab недоступен ({reason}). Проверьте, что ячейка сервера в Colab запущена.") from e


def connect(url: str, token: str = "") -> dict:
    """Check the Colab server and make it the active Qwen backend (no restart needed)."""
    url = _normalize_url(url)
    if not url:
        raise ValueError("Пустая ссылка")
    h = _request("GET", "/health", url=url, token=token, timeout=15)
    if not h.get("ok"):
        raise ValueError(f"Сервер ответил, но не готов: {h}")
    if h.get("auth") is False:
        raise ValueError("Неверный токен: возьмите ссылку целиком из вывода ячейки Colab.")
    with _lock:
        settings.qwen_url, settings.qwen_token = url, token
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps({"url": url, "token": token, "connected_at": time.time()}), encoding="utf-8")
        _status_cache["at"] = 0.0
    log.info("Qwen connected: %s (%s)", url, h.get("model"))
    return status(force=True)


def disconnect() -> None:
    with _lock:
        settings.qwen_url, settings.qwen_token = "", ""
        _STATE_FILE.unlink(missing_ok=True)
        _status_cache["at"] = 0.0


def status(force: bool = False) -> dict:
    """Live Colab status, cached for a few seconds so /api/health stays fast."""
    if not settings.qwen_url:
        return {"configured": False, "online": False}
    now = time.time()
    if not force and _status_cache["value"] and now - _status_cache["at"] < 10 and \
            _status_cache["value"].get("url") == settings.qwen_url:
        return _status_cache["value"]
    out = {"configured": True, "url": settings.qwen_url}
    try:
        h = _request("GET", "/health", timeout=6)
        out.update(online=bool(h.get("ok")) and h.get("auth") is not False, model=h.get("model"),
                   queued=h.get("queued"), gpu=h.get("gpu"), auth=h.get("auth"))
        if h.get("auth") is False:
            out["error"] = "Неверный токен"
    except Exception as e:  # noqa: BLE001 - surfaced in the UI
        out.update(online=False, error=str(e))
    _status_cache.update(at=now, value=out)
    return out


def generate(messages: list[dict], sample: bool = False) -> dict:
    """Submit a job and wait for it. Survives a Colab restart / new tunnel URL: if the job is lost or the URL
    changes (user opened a new link), it is resubmitted. Fails if Colab stays unreachable for a while."""
    body = {"messages": messages, "max_new_tokens": settings.qwen_max_new_tokens, "sample": sample}
    deadline = time.time() + settings.qwen_timeout
    last_ok = time.time()
    job_id, job_url = None, None
    while time.time() < deadline:
        try:
            if job_id is None or job_url != settings.qwen_url:
                job_url = settings.qwen_url
                job_id = _request("POST", "/jobs", body)["id"]
            time.sleep(3)
            r = _request("GET", f"/jobs/{job_id}")
            last_ok = time.time()
            if r.get("status") == "done":
                return r
            if r.get("status") == "error":
                raise RuntimeError(f"Ошибка генерации в Colab: {r.get('error')}")
        except ColabUnreachable as e:
            if time.time() - last_ok > settings.qwen_offline_grace:
                raise RuntimeError(f"{e} Оценка прервана — перезапустите её после подключения.") from e
            time.sleep(5)
        except RuntimeError as e:
            if getattr(e, "status", None) == 404:  # Colab restarted and forgot the job
                job_id = None
                continue
            raise
    raise RuntimeError(f"Colab не ответил за {settings.qwen_timeout} с.")


# =============================================================================================
# Facts computed in code (trusted; given to the model and to the rules)
# =============================================================================================

END_CLIENT = ["клиент покинул", "клиент завершил", "соединение с клиентом прервано", "байланыс үзілді", "чаттан шықты",
              "customer left", "connection to customer lost", "client left", "customer ended"]
END_OPERATOR = ["оператор закрыл чат", "оператор чатты жапты", "agent closed the chat"]
VERIFIED = ["личность клиента подтверждена", "жеке басы расталды", "identity verified"]
HOLD_ON, HOLD_OFF = ["находится на удержании"], ["больше не находится на удержании"]


def _has(text: str, keys: list[str]) -> bool:
    t = text.lower()
    return any(k in t for k in keys)


def _sec(ts: str) -> float | None:
    ts = (ts or "").strip()
    if not ts:
        return None
    if "T" in ts:
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    parts = ts.split(":")
    if all(p.isdigit() for p in parts) and len(parts) in (2, 3):
        return sum(int(p) * 60**i for i, p in enumerate(reversed(parts)))
    return None


def render_dialogue(turns: list[dict], channel: str) -> str:
    """`[i] [time] (пауза N с) speaker: text`; waits shown only before operator replies (>=60 s chat, >=10 s call)."""
    threshold = 10 if channel == "voice call" else 60
    lines, prev = [], None
    for t in turns:
        ts = t.get("timestamp", "") or ""
        s = _sec(ts)
        shown = ts[11:19] if "T" in ts else ts
        gap = ""
        if prev is not None and s is not None and t["speaker"] == "operator" and s - prev >= threshold:
            gap = f" (пауза {int(s - prev)} с)"
        lines.append(f"[{t['idx']}] [{shown}]{gap} {t['speaker']}: {t['text']}")
        if s is not None:
            prev = s
    return "\n".join(lines)


def compute_facts(turns: list[dict]) -> dict:
    op = [t["idx"] for t in turns if t["speaker"] == "operator"]
    f = {
        "first_operator_msg": op[0] if op else None,
        "last_operator_msg": op[-1] if op else None,
        "verified_msg": next((t["idx"] for t in turns if t["speaker"] == "system" and _has(t["text"], VERIFIED)), None),
        "end_msg": None,
        "end_by": None,
        "holds": [],
    }
    for t in turns:
        if t["speaker"] != "system":
            continue
        if _has(t["text"], END_CLIENT):
            f.update(end_msg=t["idx"], end_by="client")
        elif _has(t["text"], END_OPERATOR):
            f.update(end_msg=t["idx"], end_by="operator")
        if _has(t["text"], HOLD_OFF) and f["holds"] and f["holds"][-1][1] is None:
            start = turns[f["holds"][-1][0]]
            a, b = _sec(start.get("timestamp", "")), _sec(t.get("timestamp", ""))
            f["holds"][-1][1] = t["idx"]
            f["holds"][-1][2] = int(b - a) if a is not None and b is not None else None
        elif _has(t["text"], HOLD_ON):
            f["holds"].append([t["idx"], None, None])
    return f


def facts_text(turns: list[dict], f: dict) -> str:
    out = []
    if f["first_operator_msg"] is not None:
        out.append(f"- Первая реплика оператора: [{f['first_operator_msg']}]")
    else:
        out.append("- Оператор не писал.")
    if f["last_operator_msg"] is not None:
        out.append(f"- Последняя реплика оператора: [{f['last_operator_msg']}] «{turns[f['last_operator_msg']]['text']}»")
    if f["end_msg"] is not None:
        who = "клиент ушёл / связь оборвалась" if f["end_by"] == "client" else "оператор закрыл чат"
        out.append(f"- Завершение: [{f['end_msg']}] «{turns[f['end_msg']]['text']}» — {who}.")
    else:
        out.append("- Системного сообщения о завершении нет.")
    out.append(f"- Системное подтверждение личности: [{f['verified_msg']}]." if f["verified_msg"] is not None
               else "- Системного подтверждения личности в диалоге НЕТ.")
    for a, b, s in f["holds"]:
        out.append(f"- Удержание на линии: [{a}]→[{b}]" + (f", {s} с." if s is not None else "."))
    return "\n".join(out)


# =============================================================================================
# Prompt
# =============================================================================================


def scale_values(c: Criterion) -> list[int]:
    if c.type == "binary":
        return []
    keys = sorted({int(k) for k in c.scale.labels if re.fullmatch(r"-?\d+", str(k))}, reverse=True)
    return keys or list(range(c.scale.max, c.scale.min - 1, -1))


def render_checklist(config: ChecklistConfig, channel: str) -> str:
    out = []
    for c in config.criteria:
        vals = [str(v) for v in scale_values(c)] if c.type == "scale" else ["Да", "Нет"]
        out.append(f"КРИТЕРИЙ {c.id} — {c.title.upper()}  (допустимо: {', '.join(vals)}"
                   + (", N/A" if c.na_allowed else "; N/A запрещено") + ")")
        if c.description:
            out.append(c.description)
        if c.type == "scale":
            for k, v in sorted(c.scale.labels.items(), key=lambda kv: -int(kv[0]) if str(kv[0]).lstrip("-").isdigit() else 0):
                out.append(f"  {k}: {v}")
        if c.na_allowed and c.na_rule:
            out.append("N/A — " + c.na_rule)
        if c.critical and c.critical_rule and c.critical_rule not in c.description:
            out.append("Грубая ошибка: " + c.critical_rule)
        if channel == "voice call" and c.voice_rule:
            out.append("ДЛЯ ЗВОНКА: " + c.voice_rule)
        for name, fld in c.check_fields.items():
            out.append(f"  поле {name} ({fld.type}): {fld.instruction}")
        out.append("")
    return "\n".join(out)


_PLACEHOLDER = {
    "bool": "<true или false>",
    "list[int]": "<список номеров реплик, [] если таких нет>",
    "int|null": "<номер реплики или null>",
    "list[str]": "<список строк, [] если ничего>",
}


_OBJ_HINTS = {
    "msg": "<номер реплики (число), где задан вопрос>",
    "question": "<вопрос кратко>",
    "status": "<закрыт|следующий шаг|открыт-главный|открыт-второстепенный>",
    "repeat_of": "<номер более ранней реплики с тем же вопросом или null>",
}


def _placeholder(fld) -> object:
    """Format hint for a field. Never a plausible value: a small model copies example values verbatim,
    and the rules would then score every dialogue the same."""
    if fld.type == "list[obj]":
        sample = fld.example[0] if isinstance(fld.example, list) and fld.example and isinstance(fld.example[0], dict) else {}
        return [{k: _OBJ_HINTS.get(k, f"<{k}>") for k in sample} or "<объект>"]
    return _PLACEHOLDER.get(fld.type, "<значение>")


def _score_hint(c: Criterion) -> str:
    vals = [str(v) for v in scale_values(c)] if c.type == "scale" else ["Да", "Нет"]
    return "<" + "|".join(vals + (["N/A"] if c.na_allowed else [])) + ">"


def build_messages(config: ChecklistConfig, turns: list[dict], meta: dict, facts: dict) -> list[dict]:
    example = []
    for c in config.criteria:
        e: dict = {"id": c.id}
        for name, fld in c.check_fields.items():
            e[name] = _placeholder(fld)
        e.update({"explanation": "<до 20 слов>", "evidence_ids": "<номера реплик>", "score": _score_hint(c)})
        example.append(e)
    ids = [c.id for c in config.criteria]
    shape = {"conversation_id": meta.get("id"), "criteria": example,
             "summary": "<1–2 предложения: что хотел клиент и чем закончилось>"}
    user = f"""ЧЕК-ЛИСТ:
{render_checklist(config, meta.get("channel", ""))}
ФАКТЫ:
{facts_text(turns, facts)}

ДИАЛОГ (conversation_id: {meta.get("id")}, канал: {meta.get("channel")}, язык: {meta.get("language")}; номера реплик с 0):
{render_dialogue(turns, meta.get("channel", ""))}

Верни JSON такой структуры. В угловых скобках — описание, а НЕ значение: замени каждое на ответ по ЭТОМУ диалогу.
{json.dumps(shape, ensure_ascii=False)}
Ровно {len(ids)} объектов в criteria с id {json.dumps(ids, ensure_ascii=False)}, затем summary."""
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


# =============================================================================================
# Parsing and normalization
# =============================================================================================

_MAP = {"да": "Да", "yes": "Да", "иә": "Да", "true": "Да", "нет": "Нет", "no": "Нет", "жоқ": "Нет", "false": "Нет",
        "n/a": "N/A", "na": "N/A", "н/д": "N/A", "-": "N/A", "—": "N/A"}


def extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M)
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        raise ValueError(f"Модель вернула не JSON: {text[:200]!r}")
    return json.loads(re.sub(r",\s*([}\]])", r"\1", text[s : e + 1]))


def normalize_score(v, c: Criterion):
    """Model/rule score -> "Да" | "Нет" | "N/A" | int (scale). Raises ValueError if not allowed."""
    if isinstance(v, bool):
        v = "Да" if v else "Нет"
    if v is None:
        v = "N/A"
    if isinstance(v, str):
        s = v.strip()
        v = int(float(s)) if re.fullmatch(r"-?\d+(\.0+)?", s) else _MAP.get(s.lower(), s)
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if v == "N/A":
        if not c.na_allowed:
            raise ValueError(f"критерий {c.id}: N/A запрещено")
        return v
    if c.type == "binary":
        if v in (1, 0):
            return "Да" if v == 1 else "Нет"
        if v not in ("Да", "Нет"):
            raise ValueError(f"критерий {c.id}: недопустимая оценка {v!r}")
        return v
    if not isinstance(v, int):
        raise ValueError(f"критерий {c.id}: недопустимая оценка {v!r}")
    return min(scale_values(c), key=lambda a: abs(a - v))


def _ids(v, n: int) -> list[int]:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            v = re.findall(r"\d+", v)
    if not isinstance(v, list):
        v = [v] if v is not None else []
    return [int(i) for i in v if str(i).strip().lstrip("-").isdigit() and 0 <= int(i) < n]


def _bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "да", "yes", "1")
    return bool(v)


class Unfilled(ValueError):
    """The model left a field empty or copied the format hint instead of answering."""


def _is_hint(v) -> bool:
    return isinstance(v, str) and v.strip().startswith("<")


def _int_or_none(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return None


def coerce_field(v, typ: str, n: int):
    if v is None and typ != "int|null" or _is_hint(v):
        raise Unfilled(typ)
    if isinstance(v, list) and any(_is_hint(i) for i in v):
        raise Unfilled(typ)
    if typ == "list[obj]" and isinstance(v, list):
        out = []
        for q in v:
            if not isinstance(q, dict):
                continue
            if any(_is_hint(x) for x in q.values()):
                raise Unfilled(typ)
            q = dict(q)
            for k in ("msg", "repeat_of"):
                if k in q:
                    q[k] = _int_or_none(q[k])
            out.append(q)
        return out
    if typ == "bool":
        return _bool(v)
    if typ == "list[int]":
        return _ids(v, n)
    if typ == "int|null":
        ids = _ids(v, n)
        return ids[0] if ids else None
    if typ in ("list[str]", "list[obj]"):
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                v = [v] if v.strip() and v.strip() not in ("[]", "-", "нет") else []
        return v if isinstance(v, list) else []
    return v


def parse_run(text: str, config: ChecklistConfig, n: int) -> tuple[list[dict], str]:
    """Returns one dict per criterion found (missing criteria are skipped) and the summary."""
    res = extract_json(text)
    by_id = {str(x.get("id", "")).strip(): x for x in res.get("criteria") or [] if isinstance(x, dict)}
    crit = []
    for c in config.criteria:
        x = by_id.get(c.id)
        if x is None:
            continue
        x = dict(x, id=c.id, cid=c.id)
        try:
            x["score"] = normalize_score(x.get("score"), c)
        except ValueError as e:
            x["score"], x["score_error"] = None, str(e)
        x["evidence_ids"] = _ids(x.get("evidence_ids", []), n) if not _is_hint(x.get("evidence_ids")) else []
        unfilled = []
        for name, fld in c.check_fields.items():
            try:
                x[name] = coerce_field(x.get(name), fld.type, n)
            except Unfilled:
                unfilled.append(name)
                x[name] = coerce_field(fld.example, fld.type, n) if fld.example is not None else None
        if unfilled:
            x["unfilled"] = unfilled
        if _is_hint(x.get("explanation")):
            x["explanation"] = ""
        crit.append(x)
    if len(crit) < (len(config.criteria) + 1) // 2:
        raise ValueError(f"Модель вернула только {len(crit)} из {len(config.criteria)} критериев")
    return crit, str(res.get("summary") or "").strip()


# =============================================================================================
# Rules: fields + facts -> final score. Return None to keep the model's score.
# Other criteria are found by their fields (not ids), so renumbering or swapping criteria still works.
# =============================================================================================


def r_need(x, run, f, meta):
    if x["reasked_known"]:
        return "Нет"
    if not x["missing_info"]:
        return "N/A"
    return "Да" if x["operator_clarified"] else "Нет"


def _unverified(x, f):
    msgs = sorted(set(x["account_info_msgs"]) | set(x["account_action_msgs"]))
    v = [i for i in (f["verified_msg"], x.get("verbal_verification_msg")) if i is not None]
    v = min(v) if v else None
    return msgs, [m for m in msgs if v is None or m < v]


def r_identification(x, run, f, meta):
    msgs, bad = _unverified(x, f)
    if not msgs:
        return "N/A"
    x["evidence_ids"] = sorted(set(x["evidence_ids"]) | set(bad or msgs))
    if bad:
        x["rule_reason"] = ("Сведения о счёте / действия по счёту до подтверждения личности: "
                            + ", ".join(f"#{i}" for i in bad) + ".")
    return "Нет" if bad else "Да"


def r_consultation(x, run, f, meta):
    if x["score"] == "N/A" or x["score"] is None:
        return None
    qs = [q for q in x["client_questions"] if isinstance(q, dict)]
    if not qs:
        return None
    repeated = {q.get("repeat_of") for q in qs if isinstance(q.get("repeat_of"), int)}
    live = [q for q in qs if q.get("msg") not in repeated]  # latest version of each question
    st = [str(q.get("status", "")).lower() for q in live]
    if x["wrong_info_msgs"] and x["score"] in (0, 5):
        return x["score"]
    if any("главн" in s for s in st):
        return x["score"] if x["score"] in (0, 5, 10) else 10
    if any("второстеп" in s for s in st):
        return 15
    if any(s.startswith("открыт") for s in st):
        return x["score"] if x["score"] in (0, 5, 10) else 10
    return 20


def r_dialog_management(x, run, f, meta):
    if meta.get("channel") == "voice call" or x["score"] == 0:
        return None  # calls: the model judges
    c4 = next((r for r in run if "client_questions" in r and not r.get("unfilled")), None)
    fo = f["first_operator_msg"] or 0  # only re-asks AFTER the operator joined count
    repeats = [q for q in (c4["client_questions"] if c4 else []) if isinstance(q, dict)
               and isinstance(q.get("repeat_of"), int) and isinstance(q.get("msg"), int) and q["msg"] > fo]
    if repeats:
        x["evidence_ids"] = sorted(set(x["evidence_ids"]) | {q["msg"] for q in repeats})
        return 4
    return 7 if x["unclear_operator_msgs"] else 10


def r_closing(x, run, f, meta):
    if f["last_operator_msg"] is None:
        return "N/A"
    if f["end_by"] == "client" and not x["has_farewell"]:
        return "N/A"
    x["evidence_ids"] = sorted(set(x["evidence_ids"]) | {f["last_operator_msg"]})
    return "Да" if x["has_farewell"] and x["has_summary_or_offer"] else "Нет"


# A gross "disclosure" must be about THIS client's account. The 4B model also tags general answers
# ("D account money can't buy stock", "see the company website") as account info; those stay a criterion-3
# matter at most and never become a gross error.
_PERSONAL = re.compile(r"your|you have|you've|ваш|у вас|вам|сіздің|сізде|сізге|шотыңыз", re.I)


def r_gross_errors(x, run, f, meta):
    """Criterion is phrased "no gross errors": «Нет» = a violation was found."""
    c3 = next((r for r in run if "account_info_msgs" in r and not r.get("unfilled")), None)
    turns = meta.get("turns") or []
    leaked = [m for m in _unverified(c3, f)[1] if m in c3["account_info_msgs"]] if c3 else []  # disclosure only
    leaked = [m for m in leaked if m < len(turns) and _PERSONAL.search(turns[m]["text"])]
    hits = sorted(set(x["insult_msgs"]) | set(x["private_offer_msgs"]) | set(leaked))
    reasons = []
    for label, ids in (("оскорбление клиента", x["insult_msgs"]), ("предложение услуги в обход компании", x["private_offer_msgs"]),
                       ("сведения о счёте раскрыты до подтверждения личности", leaked)):
        if ids:
            reasons.append(f"{label} ({', '.join(f'#{i}' for i in sorted(ids))})")
    if hits:
        x["evidence_ids"] = sorted(set(x["evidence_ids"]) | set(hits))
        x["rule_reason"] = "Найдено: " + "; ".join(reasons) + "."
    return "Нет" if hits else "Да"


RULES = {"need": r_need, "identification": r_identification, "consultation": r_consultation,
         "dialog_management": r_dialog_management, "closing": r_closing, "gross_errors": r_gross_errors}


def apply_rules(run: list[dict], config: ChecklistConfig, facts: dict, meta: dict) -> list[dict]:
    crit = {c.id: c for c in config.criteria}
    for x in run:
        c = crit[x["cid"]]
        x["model_score"], x["derived_by"] = x["score"], "model"
        rule = RULES.get(c.derive or "")
        if not rule:
            continue
        if x.get("unfilled"):  # rule inputs unreliable -> keep the model's own score
            x["rule_error"] = "модель не заполнила поля " + ", ".join(x["unfilled"])
            continue
        try:
            s = rule(x, run, facts, meta)
            if s is not None:
                x["score"], x["derived_by"] = normalize_score(s, c), f"rule:{c.derive}"
        except Exception as e:  # noqa: BLE001 - a broken rule must not kill the evaluation
            x["rule_error"] = f"{type(e).__name__}: {e}"
            log.warning("rule %s failed on criterion %s: %s", c.derive, c.id, e)
    return run


# =============================================================================================
# Evaluation of one dialogue
# =============================================================================================


def _one_run(messages, config, turns, facts, meta, sample: bool) -> tuple[list[dict], str, dict]:
    last_err = None
    for attempt in range(2):  # retry once; the retry samples so it doesn't repeat the same broken output
        r = generate(messages, sample=sample or attempt > 0)
        try:
            run, summary = parse_run(r["text"], config, len(turns))
            return apply_rules(run, config, facts, meta), summary, r
        except (ValueError, KeyError, TypeError) as e:
            last_err = e
            log.warning("Unparseable Qwen output for %s (attempt %d): %s", meta.get("id"), attempt + 1, e)
    raise RuntimeError(f"Не удалось разобрать ответ модели: {last_err}")


def _to_value(c: Criterion, score):
    if score is None or score == "N/A":
        return None
    if c.type == "binary":
        return 1 if score == "Да" else 0
    return int(score)


def evaluate(config: ChecklistConfig, turns: list[dict], meta: dict) -> dict:
    facts = compute_facts(turns)
    messages = build_messages(config, turns, meta, facts)
    meta = {**meta, "turns": turns}  # rules may look at message text
    n_runs = max(1, settings.qwen_runs)
    runs, summaries, gens, last_err = [], [], [], None
    for _ in range(n_runs):
        try:
            run, summary, g = _one_run(messages, config, turns, facts, meta, sample=n_runs > 1)
        except RuntimeError as e:
            last_err = e
            continue
        runs.append({x["cid"]: x for x in run})
        summaries.append(summary)
        gens.append(g)
    if not runs:
        raise last_err or RuntimeError("Все прогоны модели завершились ошибкой")

    raw_items = []
    for c in config.criteria:
        votes = collections.Counter(str(run[c.id]["score"]) for run in runs if c.id in run)
        if not votes:
            continue
        top, cnt = votes.most_common(1)[0]
        x = next(run[c.id] for run in runs if c.id in run and str(run[c.id]["score"]) == top)
        agreement = cnt / len(runs)
        score = x["score"]
        if score is None:  # invalid model score and no rule: lowest value, flagged
            score = "N/A" if c.na_allowed else ("Нет" if c.type == "binary" else c.scale.min)
        value = _to_value(c, score)
        explanation = str(x.get("explanation") or "").strip()
        notes = []
        if x.get("score_error"):
            notes.append(f"Недопустимая оценка модели ({x['score_error']}).")
        if x["derived_by"] != "model" and x["model_score"] != x["score"]:
            ms = x["model_score"] if x["model_score"] is not None else "—"
            if x.get("rule_reason"):
                explanation = x["rule_reason"]
            notes.append(f"Оценка выставлена правилом по полям модели (модель ответила «{ms}»).")
        if x.get("rule_error"):
            notes.append(f"Правило не сработало: {x['rule_error']}.")
        ids = x["evidence_ids"]
        raw_items.append(
            {
                "criterion_id": c.id,
                "applicable": value is not None,
                "value": value,
                "na_reason": (c.na_rule if x["derived_by"] != "model" and x["model_score"] != "N/A" else explanation or c.na_rule)
                if value is None else "",
                "explanation": " ".join([explanation, *notes]).strip(),
                "evidence": [{"turn": i, "quote": turns[i]["text"][:200]} for i in ids],
                "checked_from_turn": min(ids) if ids else facts["first_operator_msg"],
                "checked_to_turn": max(ids) if ids else facts["last_operator_msg"],
                "confidence": "low" if agreement < 1 or x.get("score_error") else "medium",
                "derived_by": x["derived_by"],
                "model_score": x["model_score"],
                "agreement": round(agreement, 2),
                "fields": {k: x.get(k) for k in c.check_fields},
            }
        )
    return {
        "items": raw_items,
        "summary": next((s for s in summaries if s), ""),
        "facts": facts,
        "usage": {
            "input_tokens": sum(g.get("input_tokens", 0) for g in gens),
            "output_tokens": sum(g.get("output_tokens", 0) for g in gens),
            "gpu_seconds": round(sum(g.get("seconds") or 0 for g in gens), 1),
            "runs": len(runs),
            "cost_usd": 0.0,
            "raw_output": gens[0]["text"] if gens else "",
        },
    }
