# FreedomDataChallenge — CQA

Checklist-based quality evaluation of contact-centre dialogues. Website + API run on your computer;
the team Qwen model (CQA_evaluator_v3 logic) runs on a Colab GPU.

```
Browser ─► backend :8000 (FastAPI + SQLite, builds prompts, facts, rules, scores) ─► tunnel ─► Colab (Qwen3-4B, GPU)
```

## Run

1. **Website** (once per computer):
   ```
   cp .env.example .env            # EVALUATOR=qwen is the default
   cd backend
   py -m venv .venv && .venv/Scripts/python -m pip install fastapi "uvicorn[standard]" sqlalchemy pydantic-settings python-multipart anthropic
   .venv/Scripts/python -m uvicorn app.main:app --port 8000
   ```
   or `docker compose up` from the repo root.
2. **Qwen**: open `colab/CQA_qwen_server.ipynb` in Colab
   ([open from GitHub](https://colab.research.google.com/github/Sileeenceee/FreedomDataChallenge/blob/main/colab/CQA_qwen_server.ipynb)),
   choose a **T4 GPU** runtime, run the cell.
3. Click the link the cell prints (`http://localhost:8000/#/connect?url=…&token=…`). The site connects
   to Qwen and the header chip turns green: **● Qwen онлайн**. The connection is saved, so restarting
   the backend keeps it. A new Colab run means a new link; click it again, or click the chip and paste the link.

If Colab drops mid-batch, running evaluations wait up to `QWEN_OFFLINE_GRACE` seconds (default 180) for
you to reconnect, then resubmit to the new URL by themselves.

## How Qwen evaluation works (v3)

`backend/app/services/qwen.py`, ported from `CQA_evaluator_v3.ipynb`:
- **Facts computed in code**: first/last operator message, how the dialogue ended, system identity
  confirmation, holds, waits before operator replies. These go into the prompt as trusted facts.
- **Typed fields** per criterion (`check_fields` in the checklist) that the model fills before scoring.
- **Rules** (`derive`) turn fields + facts into the final score. Each criterion in the UI shows whether
  the rule or the model decided it.
- One retry on unparseable output. `QWEN_RUNS=3` turns on sampled majority voting.

The checklist (with fields and rules) is edited on the **Чек-листы** page; changes reach the prompt directly.

Tests: `cd backend && .venv/Scripts/python -m pytest -q` (includes an end-to-end run against a fake Colab server).
