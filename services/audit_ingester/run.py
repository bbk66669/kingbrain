import logging
from fastapi import FastAPI
import uvicorn
import os
from pathlib import Path

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("kb-audit-ingester")

APP = FastAPI(title="kb-audit-ingester", version="0.1.0")

@APP.get("/healthz")
async def healthz():
    return {"status": "ok"}

def ensure_backlog():
    repo_root = os.getenv("REPO_ROOT", "/srv/kingbrain")
    backlog = Path(repo_root) / ".collab" / "audit" / "backlog.jsonl"
    backlog.parent.mkdir(parents=True, exist_ok=True)
    backlog.touch(exist_ok=True)
    log.info("backlog file at %s", backlog)

if __name__ == "__main__":
    ensure_backlog()
    uvicorn.run(APP, host="0.0.0.0", port=8082)
