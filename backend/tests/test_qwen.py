"""Qwen v3 pipeline against a fake Colab server (same HTTP protocol as colab/qwen_server.py)."""

import json
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/test.db")

from app.checklist import ChecklistConfig
from app.config import settings
from app.default_checklist import DEFAULT_CHECKLIST
from app.services import qwen

TOKEN = "secret"
TURNS = [
    {"idx": 0, "timestamp": "00:00", "speaker": "client", "text": "Почему у меня ограничение на счёте?"},
    {"idx": 1, "timestamp": "00:05", "speaker": "bot", "text": "Соединяю с оператором."},
    {"idx": 2, "timestamp": "02:10", "speaker": "operator", "text": "Здравствуйте! Меня зовут Алина."},
    {"idx": 3, "timestamp": "02:30", "speaker": "operator", "text": "На вашем счёте ограничение из-за просроченного документа."},
    {"idx": 4, "timestamp": "02:50", "speaker": "client", "text": "Понятно, спасибо"},
    {"idx": 5, "timestamp": "03:00", "speaker": "operator", "text": "Обновите документ в приложении. Хорошего дня!"},
    {"idx": 6, "timestamp": "03:05", "speaker": "system", "text": "Оператор закрыл чат."},
]

MODEL_OUTPUT = {
    "conversation_id": "q-1",
    "criteria": [
        {"id": "1", "explanation": "Поздоровалась и представилась.", "evidence_ids": [2], "score": "Да"},
        {"id": "2", "missing_info": [], "operator_clarified": False, "reasked_known": False,
         "explanation": "Уточнять не требовалось.", "evidence_ids": [0], "score": "Да"},
        # account info disclosed at #3 with no identity check -> rule: «Нет», and gross error (disclosure)
        {"id": "3", "account_info_msgs": [3], "account_action_msgs": [], "verbal_verification_msg": None,
         "explanation": "Идентификация не требовалась.", "evidence_ids": [], "score": "N/A"},
        {"id": "4", "client_questions": [{"msg": 0, "question": "почему ограничение", "status": "закрыт", "repeat_of": None}],
         "wrong_info_msgs": [], "explanation": "Причина названа.", "evidence_ids": [3], "score": "15"},
        {"id": "5", "explanation": "Вежлива.", "evidence_ids": [5], "score": "Да"},
        {"id": "6", "unclear_operator_msgs": [], "explanation": "Ясно.", "evidence_ids": [3], "score": 10},
        {"id": "7", "has_farewell": True, "has_summary_or_offer": True, "explanation": "Итог и прощание.",
         "evidence_ids": [5], "score": "Да"},
        {"id": "8", "insult_msgs": [], "private_offer_msgs": [], "explanation": "Нарушений нет.",
         "evidence_ids": [], "score": "Да"},
    ],
    "summary": "Клиент спросил про ограничение, оператор назвал причину.",
}


class FakeColab(BaseHTTPRequestHandler):
    jobs: dict = {}
    outputs: list = []

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth(self):
        return self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "model": "fake-qwen", "queued": 0, "gpu": True, "auth": self._auth()})
        if not self._auth():
            return self._send(401, {"detail": "bad token"})
        jid = self.path.rsplit("/", 1)[-1]
        if jid not in self.jobs:
            return self._send(404, {"detail": "unknown job"})
        return self._send(200, self.jobs[jid])

    def do_POST(self):
        if not self._auth():
            return self._send(401, {"detail": "bad token"})
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert "ФАКТЫ" in body["messages"][1]["content"]
        text = self.outputs.pop(0) if self.outputs else json.dumps(MODEL_OUTPUT, ensure_ascii=False)
        jid = str(len(self.jobs) + 1)
        self.jobs[jid] = {"status": "done", "text": text, "input_tokens": 100, "output_tokens": 50, "seconds": 1.0}
        return self._send(200, {"id": jid})


def _serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeColab)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


CFG = ChecklistConfig.model_validate(DEFAULT_CHECKLIST)
META = {"id": "q-1", "channel": "agent chat", "language": "RU"}


def test_facts_and_prompt():
    f = qwen.compute_facts(TURNS)
    assert f["first_operator_msg"] == 2 and f["last_operator_msg"] == 5
    assert f["end_by"] == "operator" and f["verified_msg"] is None
    msgs = qwen.build_messages(CFG, TURNS, META, f)
    user = msgs[1]["content"]
    assert "(пауза 125 с) operator" in user  # wait before the first operator reply is shown
    assert "поле account_info_msgs (list[int])" in user
    assert "Системного подтверждения личности в диалоге НЕТ" in user


def test_wrong_token_is_rejected():
    srv, url = _serve()
    try:
        try:
            qwen.connect(url, "wrong")
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "токен" in str(e)
    finally:
        srv.shutdown()


def test_end_to_end_rules():
    srv, url = _serve()
    try:
        st = qwen.connect(url, TOKEN)
        assert st["online"] and st["model"] == "fake-qwen"
        FakeColab.outputs = ["not json at all"]  # first answer is broken -> one retry
        out = qwen.evaluate(CFG, TURNS, META)
    finally:
        qwen.disconnect()
        srv.shutdown()
    items = {i["criterion_id"]: i for i in out["items"]}
    assert out["summary"].startswith("Клиент спросил")
    assert items["2"]["value"] is None and items["2"]["derived_by"] == "rule:need"  # nothing missing -> N/A
    assert items["3"]["value"] == 0 and items["3"]["model_score"] == "N/A"  # disclosed before verification
    assert items["4"]["value"] == 20  # all questions closed
    assert items["7"]["value"] == 1
    assert items["8"]["value"] == 0 and 3 in [e["turn"] for e in items["8"]["evidence"]]  # disclosure = gross error
    assert "раскрыты до подтверждения личности (#3)" in items["8"]["explanation"]  # rule reason, not "Нарушений нет"
    assert items["1"]["derived_by"] == "model"


def test_api_connect_and_evaluate():
    from fastapi.testclient import TestClient

    from app.main import app

    srv, url = _serve()
    old = settings.evaluator
    settings.evaluator = "qwen"
    try:
        with TestClient(app) as client:
            r = client.post("/api/evaluations", json={"conversation_ids": ["x"]})
            assert r.status_code == 409  # not connected -> clear error instead of silent mock
            r = client.post("/api/qwen/connect", json={"url": url, "token": TOKEN})
            assert r.status_code == 200, r.text
            assert client.get("/api/health").json()["qwen"]["online"] is True
            rec = {"conversation_id": "q-1", "channel": "agent chat", "language": "RU",
                   "transcript": [{k: t[k] for k in ("timestamp", "speaker", "text")} for t in TURNS]}
            files = {"file": ("d.json", json.dumps([rec], ensure_ascii=False).encode(), "application/json")}
            assert client.post("/api/upload", files=files).status_code in (200, 201)
            r = client.post("/api/evaluations", json={"conversation_ids": ["q-1"]})
            assert r.status_code == 202, r.text
            bid = r.json()["batch_id"]
            for _ in range(100):
                b = client.get(f"/api/batches/{bid}").json()
                if b["finished"] == b["total"]:
                    break
                time.sleep(0.1)
            eid = client.get("/api/conversations/q-1").json()["evaluations"][0]["id"]
            ev = client.get(f"/api/evaluations/{eid}").json()
            assert ev["status"] == "done", ev.get("error")
            assert ev["evaluator"] == "qwen" and ev["result"]["critical_error"] is True
            client.post("/api/qwen/disconnect")
    finally:
        settings.evaluator = old
        srv.shutdown()


def test_general_answer_is_not_a_gross_error():
    """Model tags a general answer as account info: criterion 3 may fail, but no gross error."""
    turns = [dict(t) for t in TURNS]
    turns[3]["text"] = "Деньги на D-счёте нельзя использовать для покупки акций, смотрите торговый счёт."
    run = [{"cid": "3", "account_info_msgs": [3], "account_action_msgs": [], "verbal_verification_msg": None,
            "evidence_ids": [], "score": "N/A"},
           {"cid": "8", "insult_msgs": [], "private_offer_msgs": [], "evidence_ids": [], "score": "Да"}]
    f = qwen.compute_facts(turns)
    assert qwen.r_gross_errors(run[1], run, f, {"turns": turns}) == "Да"
    turns[3]["text"] = "На вашем счёте ограничение."
    assert qwen.r_gross_errors(run[1], run, f, {"turns": turns}) == "Нет"
