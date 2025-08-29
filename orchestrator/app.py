# orchestrator/app.py
import os, json
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

try:
    from orchestrator.api import orchestrator
except Exception:
    from api import orchestrator  # 本地兜底

app = FastAPI()

def _mode() -> str:
    try:
        return orchestrator.mode.value
    except Exception:
        forced = os.getenv("KB_MODE","").strip().upper()
        if forced in ("FAKE","REAL"): return forced
        for k in ("OPENAI_API_KEY","ANTHROPIC_API_KEY","AZURE_OPENAI_KEY"):
            if os.getenv(k): return "REAL"
        return "FAKE"

@app.get("/kb-api/health")
async def health():
    return {"status":"ok","mode":_mode()}

@app.get("/kb-api/config")
async def config():
    cfg = orchestrator.get_config()
    return JSONResponse(cfg, headers={"x-kb-mode": cfg.get("mode","AUTO")})

async def _run_phase(req: Request, phase: str):
    body: Dict[str, Any] = {}
    try:
        if req.headers.get("content-length","0") != "0":
            body = await req.json()
    except Exception:
        body = {}
    task  = body.get("task","")
    notes = body.get("notes","")
    paths = body.get("paths")

    res = orchestrator.process_workflow(task=task, notes=notes, phase=phase, paths_to_write=paths)
    payload = {
        "workflow_id":   res.workflow_id,
        "run_id":        res.run_id,
        "phase":         res.phase,
        "written_paths": res.written_paths,
        "evidence_refs": res.evidence_refs,
        "cloudevent_ids":res.cloudevent_ids,
        "ts":            res.timestamp,
        "mode":          res.mode,
        "error":         res.error,
    }
    return JSONResponse(payload, status_code=(200 if not res.error else 400),
                        headers={"x-kb-mode": res.mode})

@app.post("/kb-api/ack")
async def ack(req: Request):
    return await _run_phase(req, "ACK")

@app.post("/kb-api/plan")
async def plan(req: Request):
    return await _run_phase(req, "PLAN")

@app.post("/kb-api/borrow")
async def borrow(req: Request):
    return await _run_phase(req, "BORROW")

@app.post("/kb-api/diff")
async def diff(req: Request):
    return await _run_phase(req, "DIFF")

@app.post("/kb-api/cr")
async def cr(req: Request):
    return await _run_phase(req, "CR")

@app.get("/kb-api/runs/{wid}")
async def runs(wid: str, wait: Optional[int]=0):
    return JSONResponse({"error": f"workflow not found for ID: {wid}", "mode": _mode()},
                        status_code=404, headers={"x-kb-mode": _mode()})
