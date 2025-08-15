import os
import time
import uuid
from fastapi import FastAPI, Request

def _determine_mode() -> str:
    # mirrors orchestrator logic but simplified
    pref = os.getenv("KB_MODE", "AUTO").upper()
    if pref in ("FAKE", "REAL"):
        return pref
    if any(os.getenv(k) for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_KEY")):
        return "REAL"
    return "FAKE"

MODE = _determine_mode()
app = FastAPI(title="kb-composer", version="0.1.0")

@app.get("/healthz")
async def health():
    return {"status": "ok", "mode": MODE}

@app.post("/plan")
async def plan(request: Request):
    body = await request.json()
    wid, rid = str(uuid.uuid4()), str(uuid.uuid4())
    result = {
        "workflow_id": wid,
        "run_id": rid,
        "result": {
            "phase": "PLAN",
            "written_paths": [],
            "evidence_refs": [],
            "cloudevent_ids": [],
            "ts": int(time.time()),
            "mode": MODE,
            "reason": "NO_LLM_KEYS" if MODE == "FAKE" else "PLACEHOLDER",
        },
    }
    return result
