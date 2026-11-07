# CQA — serve the team Qwen model from Google Colab to the CQA website (backend runs on your computer).
#
# HOW TO USE (Colab: Runtime -> Change runtime type -> T4 GPU):
#   1. Paste this whole file into ONE cell (or open colab/CQA_qwen_server.ipynb) and run it.
#   2. It prints a link like  http://localhost:8000/#/connect?url=...&token=...
#      Click it (on the computer where the backend runs) — the website connects to Qwen by itself.
# Re-running the cell only restarts the tunnel (new link); the model stays loaded.
#
# The backend builds the prompts (checklist + facts + dialogue, CQA_evaluator_v3 logic) and applies the
# rules; this cell only generates text. Jobs are queued and run one at a time on the GPU (POST /jobs, then
# GET /jobs/{id}), because a Cloudflare quick tunnel drops any single request that takes longer than ~100 s.

SITE = "http://localhost:8000"  # where the CQA website runs (change if you host it elsewhere)
MODEL_ID = "Qwen/Qwen3-4B-Instruct-2507"
USE_4BIT = False  # v3 setting: 4B in fp16 (~8 GB) fits a T4. True only for bigger models (e.g. Qwen3-8B).
PORT = 8008

import subprocess, sys

subprocess.run([sys.executable, "-m", "pip", "-q", "install", "fastapi", "uvicorn", "accelerate",
                *(["bitsandbytes>=0.46.1"] if USE_4BIT else [])], check=True)
subprocess.run(
    "test -f cloudflared || (wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/"
    "cloudflared-linux-amd64 -O cloudflared && chmod +x cloudflared)",
    shell=True, check=True,
)

import queue, re, secrets, threading, time, urllib.parse, uuid

import torch
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

# ---- model (reuses `model`/`tokenizer` if a notebook cell already loaded them) ----
if "model" not in globals() or "tokenizer" not in globals():
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    print(f"Loading {MODEL_ID} ...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    kw = dict(device_map="auto")
    if USE_4BIT:
        kw["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                                       bnb_4bit_compute_dtype=torch.float16)
    else:
        kw["dtype"] = torch.float16
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, **kw)
MODEL_NAME = getattr(model, "name_or_path", None) or MODEL_ID

# A random secret per runtime so nobody else can use your GPU through the public tunnel URL.
TOKEN = globals().get("TOKEN") or secrets.token_urlsafe(16)

api = FastAPI()
jobs: dict = globals().get("jobs", {})
todo: "queue.Queue" = globals().get("todo") or queue.Queue()


class JobRequest(BaseModel):
    messages: list[dict]
    max_new_tokens: int = 2000
    sample: bool = False


def _authorized(auth: str) -> bool:
    return auth == f"Bearer {TOKEN}"


def _check(auth: str):
    if not _authorized(auth):
        raise HTTPException(401, "bad token")


def _generate(messages, max_new_tokens, sample):
    inputs = tokenizer.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors="pt"
    ).to(model.device)
    gen = dict(max_new_tokens=max_new_tokens, use_cache=True, pad_token_id=tokenizer.eos_token_id)
    gen.update(dict(do_sample=True, temperature=0.3, top_p=0.9) if sample else dict(do_sample=False))
    t0 = time.time()
    with torch.inference_mode():
        out = model.generate(**inputs, **gen)
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
            jobs[jid] = {"status": "done", "finished": time.time(),
                         **_generate(req["messages"], req["max_new_tokens"], req["sample"])}
            print(f"[cqa] job {jid} done in {jobs[jid]['seconds']} s, {jobs[jid]['output_tokens']} tokens")
        except Exception as e:  # surfaced to the backend as an evaluation error
            jobs[jid] = {"status": "error", "error": repr(e), "finished": time.time()}
            print(f"[cqa] job {jid} failed: {e!r}")
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        # forget results nobody fetched for an hour
        for k in [k for k, v in jobs.items() if v.get("finished", time.time()) < time.time() - 3600]:
            jobs.pop(k, None)


@api.get("/health")
def health(authorization: str = Header(default="")):
    return {"ok": True, "model": MODEL_NAME, "queued": todo.qsize(), "gpu": torch.cuda.is_available(),
            "auth": _authorized(authorization)}


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


# Run once per runtime: worker + HTTP server. Re-running the cell restarts only the tunnel.
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
threading.Thread(target=lambda: [None for _ in _cqa_tunnel.stdout], daemon=True).start()  # keep pipe drained

# Wait until the tunnel actually serves /health (DNS for a fresh quick tunnel takes a few seconds).
import urllib.request

for _ in range(30):
    try:
        urllib.request.urlopen(urllib.request.Request(url + "/health", headers={"User-Agent": "cqa"}), timeout=5)
        break
    except Exception:
        time.sleep(2)

link = f"{SITE}/#/connect?" + urllib.parse.urlencode({"url": url, "token": TOKEN})
try:
    from IPython.display import HTML, display

    display(HTML(f'<p style="font-size:16px">✅ Qwen is ready. <b><a href="{link}" target="_blank">'
                 f'Open the CQA website connected to this model</a></b></p>'))
except ImportError:
    pass
print("\nOpen this link on the computer where the CQA backend runs:\n")
print(link)
print(f"\n(or put into .env:  QWEN_URL={url}  QWEN_TOKEN={TOKEN})")
print("Keep this Colab tab open; the link changes each time you re-run this cell.")
