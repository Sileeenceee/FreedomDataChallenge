import os
import tempfile
import time

import pytest

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["EVALUATOR"] = "mock"
os.environ["CQA_API_URL"] = ""

from fastapi.testclient import TestClient

from app.main import app
from app.services.data_source import normalize_transcript

RECORDS = {
    "isSuccess": True,
    "error": "",
    "data": {
        "dataset_group_id": "DEV",
        "total_records": 2,
        "records": [
            {
                "conversation_id": "t-ru-001",
                "channel": "agent chat",
                "language": "RU",
                "audio_url": None,
                "transcript": [
                    {"timestamp": "00:00", "speaker": "client", "text": "Где мой тариф?"},
                    {"timestamp": "00:02", "speaker": "bot", "text": "Здравствуйте! Я AIM."},
                    {"timestamp": "00:08", "speaker": "system", "text": "Эскалация на оператора."},
                    {"timestamp": "01:15", "speaker": "operator", "text": "Здравствуйте! Меня зовут Алина."},
                    {"timestamp": "01:28", "speaker": "client", "text": "Спасибо"},
                    {"timestamp": "02:04", "speaker": "operator", "text": "Пожалуйста. Хорошего вечера!"},
                ],
            },
            {
                "conversation_id": "t-call-001",
                "channel": "voice call",
                "language": "RU",
                "audio_url": "https://example.invalid/audio",
                "transcript": [
                    {
                        "timestamp": "2026-11-03T13:49:20+00:00",
                        "speaker": "system",
                        "text": "Агент присоединился.",
                    },
                    {"timestamp": "2026-11-03T13:49:47+00:00", "speaker": "operator", "text": "Добрый день"},
                    {
                        "timestamp": "2026-11-03T13:49:50+00:00",
                        "speaker": "system",
                        "text": "Запись и транскрибирование запущены.",
                    },
                    {"timestamp": "2026-11-03T13:50:20+00:00", "speaker": "client", "text": "Алло"},
                ],
            },
        ],
    },
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def wait_done(client, batch_id, timeout=20):
    for _ in range(timeout * 10):
        b = client.get(f"/api/batches/{batch_id}").json()
        if b["finished"] == b["total"]:
            return b
        time.sleep(0.1)
    raise AssertionError("batch did not finish")


def test_call_offsets_start_at_recording():
    turns = normalize_transcript(RECORDS["data"]["records"][1]["transcript"])
    assert [t["offset_s"] for t in turns] == [0.0, 0.0, 0.0, 30.0]
    chat = normalize_transcript(RECORDS["data"]["records"][0]["transcript"])
    assert chat[3]["offset_s"] == 75.0


def test_full_flow(client):
    assert client.get("/api/health").json()["evaluator"] == "mock"
    r = client.post("/api/conversations", json={"payload": RECORDS})
    assert r.json()["created"] == 2 and r.json()["group"] == "DEV"

    active = client.get("/api/checklists/active").json()
    assert len(active["config"]["criteria"]) == 8

    r = client.post("/api/evaluations", json={"group": "DEV"})
    assert r.status_code == 202 and r.json()["count"] == 2
    b = wait_done(client, r.json()["batch_id"])
    assert b["counts"].get("done") == 2

    conv = client.get("/api/conversations/t-ru-001").json()
    ev = client.get(f"/api/evaluations/{conv['evaluations'][0]['id']}").json()
    assert ev["status"] == "done" and ev["final_result"]["total"] is not None
    assert {i["criterion_id"] for i in ev["items"]} == {c["id"] for c in active["config"]["criteria"]}
    greeting = next(i for i in ev["items"] if i["criterion_id"] == "1")
    assert greeting["evidence"][0]["turn"] == 3 and greeting["evidence"][0]["verified"]

    # appeal -> reviewer changes the score -> total recomputed, original kept
    target = next(i for i in ev["items"] if i["applicable"])
    crit = next(c for c in active["config"]["criteria"] if c["id"] == target["criterion_id"])
    new_value = crit["scale"]["min"] if target["value"] != crit["scale"]["min"] else crit["scale"]["max"]
    r = client.post(
        f"/api/evaluations/{ev['id']}/appeals",
        json={"criterion_id": target["criterion_id"], "comment": "Не согласен"},
    )
    assert r.status_code == 201
    appeal_id = r.json()["id"]
    dup = client.post(
        f"/api/evaluations/{ev['id']}/appeals",
        json={"criterion_id": target["criterion_id"], "comment": "Ещё раз"},
    )
    assert dup.status_code == 409
    assert client.get("/api/appeals", params={"status": "open"}).json()[0]["id"] == appeal_id
    detail = client.get(f"/api/appeals/{appeal_id}").json()
    assert detail["fragment"] and detail["criterion"]["id"] == target["criterion_id"]

    bad = client.post(
        f"/api/appeals/{appeal_id}/decision", json={"decision": "change", "reason": "неверно", "value": 99}
    )
    assert bad.status_code == 400
    r = client.post(
        f"/api/appeals/{appeal_id}/decision",
        json={"decision": "change", "reason": "Пересмотрено", "value": new_value},
    )
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["status"] == "changed" and after["total_before"] == ev["final_result"]["total"]
    assert after["evaluation"]["result"] == ev["result"]  # original AI result untouched
    final_item = next(
        i for i in after["evaluation"]["final_items"] if i["criterion_id"] == target["criterion_id"]
    )
    assert final_item["value"] == new_value and final_item["overridden"]
    assert (
        client.post(
            f"/api/appeals/{appeal_id}/decision", json={"decision": "confirm", "reason": "x" * 3}
        ).status_code
        == 409
    )


def test_checklist_versioning_and_new_criterion(client):
    active = client.get("/api/checklists/active").json()
    cfg = active["config"]
    cfg["criteria"][0] = {
        "id": "new_crit",
        "section_id": cfg["criteria"][0]["section_id"],
        "title": "Новый критерий",
        "description": "Проверка",
        "type": "scale",
        "scale": {"min": 1, "max": 5, "labels": {}},
        "weight": 10,
        "na_allowed": True,
        "na_rule": "",
        "critical": False,
        "critical_rule": "",
    }
    r = client.post("/api/checklists", json={"config": cfg, "base_id": active["id"], "comment": "replace"})
    assert r.status_code == 201
    new = r.json()
    assert new["version"] == active["version"] + 1 and new["is_active"] and new["family"] == active["family"]
    r = client.post("/api/evaluations", json={"conversation_ids": ["t-ru-001"]})
    wait_done(client, r.json()["batch_id"])
    conv = client.get("/api/conversations/t-ru-001").json()
    ev = client.get(f"/api/evaluations/{conv['evaluations'][0]['id']}").json()
    assert "new_crit" in {i["criterion_id"] for i in ev["items"]}
    assert ev["checklist_id"] == new["id"]

    bad = client.post("/api/checklists", json={"config": {**cfg, "criteria": []}})
    assert bad.status_code == 400
