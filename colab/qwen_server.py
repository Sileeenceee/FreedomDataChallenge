# CQA — serve the Qwen model from Google Colab to the local backend.
#
# Paste this into ONE new cell of LLM-training.ipynb, right AFTER the cell that loads `model` and
# `tokenizer`, and run it. It prints a line like
#     QWEN_URL=https://something.trycloudflare.com
# Put that line into the project's .env and restart the backend. The URL changes every time this cell runs.
#
# The backend sends ready-made prompts (system prompt + checklist + dialogue); this cell only generates.
# Requests are queued and processed one at a time on the GPU (POST /jobs, then GET /jobs/{id}),
# because a Cloudflare quick tunnel drops any single request that takes longer than ~100 s.

import subprocess, sys
subprocess.run([sys.executable, "-m", "pip", "-q", "install", "fastapi", "uvicorn"], check=True)
subprocess.run(
    "test -f cloudflared || (wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/"
    "cloudflared-linux-amd64 -O cloudflared && chmod +x cloudflared)",
    shell=True, check=True,
)

import queue, re, threading, time, uuid

import torch
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

TOKEN = ""  # optional: set a secret here and the same value as QWEN_TOKEN in .env
PORT = 8008
MODEL_ID = globals().get("model_id", "Qwen/Qwen3-4B-Instruct-2507")

api = FastAPI()
jobs: dict[str, dict] = {}
todo: "queue.Queue[tuple[str, dict]]" = queue.Queue()


class JobRequest(BaseModel):
    messages: list[dict]
    max_new_tokens: int = 1500


def _check(auth: str):
    if TOKEN and auth != f"Bearer {TOKEN}":
        raise HTTPException(401, "bad token")


def _generate(messages, max_new_tokens):
    inputs = tokenizer.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors="pt"
    ).to(model.device)
    t0 = time.time()
    with torch.inference_mode():
        out = model.generate(
            **inputs, max_new_tokens=max_new_tokens, do_sample=False, use_cache=True,
            pad_token_id=tokenizer.eos_token_id,
        )
    n = inputs["input_ids"].shape[-1]
    return {
        "text": tokenizer.decode(out[0][n:], skip_special_tokens=True).strip(),
        "input_tokens": int(n),
        "output_tokens": int(out.shape[-1] - n),
        "seconds": round(time.time() - t0, 1),
    }


def _worker():
    while True:
        jid, req = todo.get()
        jobs[jid]["status"] = "running"
        try:
            jobs[jid] = {"status": "done", **_generate(req["messages"], req["max_new_tokens"])}
            print(f"[cqa] job {jid} done in {jobs[jid]['seconds']} s, {jobs[jid]['output_tokens']} tokens")
        except Exception as e:  # surfaced to the backend as an evaluation error
            jobs[jid] = {"status": "error", "error": repr(e)}
            print(f"[cqa] job {jid} failed: {e!r}")


@api.get("/health")
def health():
    return {"ok": True, "model": MODEL_ID, "queued": todo.qsize(), "gpu": torch.cuda.is_available()}


@api.post("/jobs")
def create_job(req: JobRequest, authorization: str = Header(default="")):
    _check(authorization)
    jid = uuid.uuid4().hex[:12]
    jobs[jid] = {"status": "queued"}
    todo.put((jid, req.model_dump()))
    return {"id": jid}


@api.get("/jobs/{jid}")
def get_job(jid: str, authorization: str = Header(default="")):
    _check(authorization)
    if jid not in jobs:
        raise HTTPException(404, "unknown job")
    return jobs[jid]


# Run once per runtime: worker + HTTP server + tunnel (re-running the cell restarts only the tunnel).
if not globals().get("_cqa_started"):
    threading.Thread(target=_worker, daemon=True).start()
    server = uvicorn.Server(uvicorn.Config(api, host="0.0.0.0", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    _cqa_started = True

if globals().get("_cqa_tunnel"):
    _cqa_tunnel.kill()
_cqa_tunnel = subprocess.Popen(
    ["./cloudflared", "tunnel", "--url", f"http://localhost:{PORT}", "--no-autoupdate"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
url = None
for line in _cqa_tunnel.stdout:
    m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
    if m:
        url = m.group(0)
        break
# keep draining cloudflared output so the pipe never blocks it
threading.Thread(target=lambda: [None for _ in _cqa_tunnel.stdout], daemon=True).start()
print("\nPut this line into .env and restart the backend:\n")
print(f"QWEN_URL={url}")
