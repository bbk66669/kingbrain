# orchestrator/app.py
import os, json
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, HTTPException
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

# —— 同步五阶段（保留） ——
@app.post("/kb-api/ack")    async def ack(req: Request):    return await _run_phase(req, "ACK")
@app.post("/kb-api/plan")   async def plan(req: Request):   return await _run_phase(req, "PLAN")
@app.post("/kb-api/borrow") async def borrow(req: Request): return await _run_phase(req, "BORROW")
@app.post("/kb-api/diff")   async def diff(req: Request):   return await _run_phase(req, "DIFF")
@app.post("/kb-api/cr")     async def cr(req: Request):     return await _run_phase(req, "CR")

# !!! 删除你之前那个返回 404 的占位 /kb-api/runs/{wid} 路由 !!!

# —— 异步（Temporal）——
from services.composer.temporal_client import submit_async as submit_wf, describe_async as describe_wf

@app.post("/kb-api/submit")
async def submit(payload: Dict[str, Any]):
    phase = (payload.get("phase") or "").upper()
    if phase not in ("ACK","PLAN","BORROW","DIFF","CR"):
        raise HTTPException(400, f"bad phase: {phase}")
    task  = payload.get("task","")
    notes = payload.get("notes","")
    ctx = {
        "repo_root": os.getenv("REPO_ROOT", "/workspace"),
        "agents_file": os.path.join(os.getenv("REPO_ROOT", "/workspace"), ".collab/agents.yaml"),
        "workflow_id": None, "run_id": None,
    }
    try:
        ids = await submit_wf(phase, task, notes, ctx)
        return JSONResponse({"queued": True, **ids})
    except Exception as e:
        raise HTTPException(503, f"temporal submit failed: {e}")

@app.get("/kb-api/runs/{wid}")
async def get_run(wid: str):
    try:
        info = await describe_wf(wid)
        return JSONResponse(info)
    except Exception as e:
        raise HTTPException(404, f"{wid}: {e}")
