import os
import json
import uuid
import time
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
import yaml
import re

# ------------------------------------------------------------
# logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("kb-orchestrator")

# ------------------------------------------------------------
# constants & env
DEFAULT_REPO_ROOT = "/srv/kingbrain"
REPO_ROOT = os.environ.get("REPO_ROOT", DEFAULT_REPO_ROOT)
KB_MODE = os.environ.get("KB_MODE", "AUTO")

NATS_URL = os.environ.get(
    "NATS_URL", "nats://kb-nats.orchestrator.svc.cluster.local:4222"
)
TEMPORAL_URL = os.environ.get(
    "TEMPORAL_URL", "temporal-frontend.orchestrator.svc.cluster.local:7233"
)

# cloud-events constants
CLOUD_EVENT_SPEC_VERSION = "1.0"
CLOUD_EVENT_SOURCE = "kb-orchestrator"

# path for audit logs
AUDIT_DIR = os.path.join(REPO_ROOT, ".collab/audit")
os.makedirs(AUDIT_DIR, exist_ok=True)

# allow-list path
ALLOWLIST_PATH = os.path.join(REPO_ROOT, ".collab/paths.allowlist.yaml")


class KBOrchestrator:
    def __init__(self):
        self.mode = self._determine_mode()
        self.nats_client = None
        self.temporal_client = None

        self._init_nats()
        self._init_temporal()

        # load allow-list
        self.allowlist = self._load_allowlist()

        logger.info("KB Orchestrator initialized in %s mode", self.mode)

    # -----------------------------------------------------------------
    # helpers

    def _determine_mode(self) -> str:
        explicit = KB_MODE.upper()
        if explicit in ("FAKE", "REAL"):
            return explicit

        has_openai = bool(os.getenv("OPENAI_API_KEY"))
        has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))
        has_azure = bool(os.getenv("AZURE_OPENAI_KEY"))
        return "REAL" if any((has_openai, has_anthropic, has_azure)) else "FAKE"

    def _init_nats(self):
        try:
            logger.info("NATS would connect to %s", NATS_URL)
            self.nats_available = True
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to init NATS: %s", exc)
            self.nats_available = False

    def _init_temporal(self):
        try:
            logger.info("Temporal would connect to %s", TEMPORAL_URL)
            self.temporal_available = True
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to init Temporal: %s", exc)
            self.temporal_available = False

    # --------------------------------------------------
    # allow-list helpers

    def _load_allowlist(self) -> Dict[str, Any]:
        try:
            with open(ALLOWLIST_PATH, "r") as fh:
                return yaml.safe_load(fh)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to load allow-list: %s", exc)
            return {"allow": [], "deny": ["**"], "writable": []}

    def _path_matches_pattern(self, path: str, pattern: str) -> bool:
        tmp = re.escape(pattern)
        tmp = tmp.replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
        if not tmp.endswith(".*") and not tmp.endswith("$"):
            tmp += "$"
        return bool(re.match(tmp, path))

    def _check_path_allowed(self, path: str) -> Tuple[bool, str]:
        # normalise and strip known prefixes
        for prefix in ("/workspace", REPO_ROOT):
            if path.startswith(prefix):
                path = path[len(prefix):]
        if not path.startswith("/"):
            path = "/" + path

        # deny dominates
        for patt in self.allowlist.get("deny", []):
            if self._path_matches_pattern(path, patt):
                return False, f"path {path} matches deny pattern {patt}"

        # must be in allow
        if not any(self._path_matches_pattern(path, patt)
                   for patt in self.allowlist.get("allow", [])):
            return False, f"path {path} is not in allow list"

        # and must be writable
        if not any(self._path_matches_pattern(path, patt)
                   for patt in self.allowlist.get("writable", [])):
            return False, f"path {path} is not in writable list"

        return True, ""

    # --------------------------------------------------
    # cloud-events helpers

    def _get_audit_file_path(self) -> str:
        today = datetime.utcnow().strftime("%Y%m%d")
        return os.path.join(AUDIT_DIR, f"events-{today}.jsonl")

    def _write_cloud_event(self, ev_type: str, workflow_id: str, data: Dict) -> str:
        ev_id = str(uuid.uuid4())
        event = {
            "id": ev_id,
            "source": CLOUD_EVENT_SOURCE,
            "specversion": CLOUD_EVENT_SPEC_VERSION,
            "type": ev_type,
            "subject": workflow_id,
            "time": datetime.utcnow().isoformat() + "Z",
            "data": data,
        }

        if self.nats_available:
            try:
                logger.info("Would publish CloudEvent (%s) to NATS", ev_type)
            except Exception as exc:  # pragma: no cover
                logger.warning("NATS publish failed: %s", exc)

        try:
            fp = self._get_audit_file_path()
            Path(fp).parent.mkdir(parents=True, exist_ok=True)
            with open(fp, "a") as fh:
                fh.write(json.dumps(event) + "\n")
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to write audit log: %s", exc)

        return ev_id

    # --------------------------------------------------
    # public helpers for Flask server

    def get_config(self) -> Dict[str, Any]:
        providers = [
            name for name, key in (
                ("openai", os.getenv("OPENAI_API_KEY")),
                ("anthropic", os.getenv("ANTHROPIC_API_KEY")),
                ("azure", os.getenv("AZURE_OPENAI_KEY")),
            ) if key
        ]

        return {
            "mode": self.mode,
            "llm_providers_detected": providers,
            "events_sink": "nats+file" if self.nats_available else "file",
            "repo_root": REPO_ROOT,
            "deps": {
                "nats": {"available": self.nats_available, "url": NATS_URL},
                "temporal": {"available": self.temporal_available, "url": TEMPORAL_URL},
            },
        }

    def get_event(self, ev_id: str) -> Optional[Dict[str, Any]]:
        try:
            files = sorted(
                (f for f in os.listdir(AUDIT_DIR) if f.startswith("events-")),
                reverse=True,
            )
            for fname in files:
                with open(os.path.join(AUDIT_DIR, fname)) as fh:
                    for line in fh:
                        try:
                            ev = json.loads(line)
                            if ev.get("id") == ev_id:
                                return ev
                        except json.JSONDecodeError:
                            continue
            return None
        except Exception as exc:  # pragma: no cover
            logger.error("Error retrieving event %s: %s", ev_id, exc)
            return None

    # --------------------------------------------------
    # main entry – process_workflow

    def _ensure_file(self, path: str, content: str = ""):
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text(content, encoding="utf-8")
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to create placeholder %s: %s", path, exc)

    def _default_written_paths(self, phase: str, wid: str) -> List[str]:
        if phase == "PLAN":
            return [
                f"{REPO_ROOT}/docs/kingbrain/PLAN/PLAN.md",
                f"{REPO_ROOT}/.collab/PLAN/manifest-{wid[:8]}.json",
            ]
        if phase == "BORROW":
            return [f"{REPO_ROOT}/docs/kingbrain/BORROW/BorrowedArtifacts.yaml"]
        if phase == "DIFF":
            return [f"{REPO_ROOT}/.collab/DIFF/diff-{wid[:8]}.patch"]
        if phase == "CR":
            return [f"{REPO_ROOT}/.collab/CR/CR-draft-{wid[:8]}.yaml"]
        # ACK or fallback
        return [f"{REPO_ROOT}/.collab/{phase}/placeholder-{wid[:8]}.txt"]

    def process_workflow(
        self,
        task: str,
        notes: str,
        phase: str,
        paths_to_write: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        workflow_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())

        # --------------------------------------------------
        # 1) guard paths
        if paths_to_write:
            for p in paths_to_write:
                ok, reason = self._check_path_allowed(p)
                if not ok:
                    ev_id = self._write_cloud_event(
                        f"kb.workflow.{phase}.rejected.v1",
                        workflow_id,
                        {"phase": phase, "mode": self.mode, "reason": reason},
                    )
                    return {
                        "error": "path not allowed",
                        "detail": reason,
                        "workflow_id": workflow_id,
                        "event_id": ev_id,
                    }

        # --------------------------------------------------
        # 2) started event
        started_id = self._write_cloud_event(
            f"kb.workflow.{phase}.started.v1",
            workflow_id,
            {"phase": phase, "mode": self.mode, "task": task, "notes": notes},
        )

        # --------------------------------------------------
        # 3) simulate processing & write placeholders
        time.sleep(0.1)  # FAKE latency

        written = paths_to_write or self._default_written_paths(phase, workflow_id)
        for fp in written:
            self._ensure_file(fp, f"# Placeholder generated for {phase}\n")

        evidence_refs = [
            "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            f"sbom:{workflow_id}",
        ]

        # --------------------------------------------------
        # 4) completed event
        completed_id = self._write_cloud_event(
            f"kb.workflow.{phase}.completed.v1",
            workflow_id,
            {
                "phase": phase,
                "mode": self.mode,
                "written": written,
                "evidenceRefs": evidence_refs,
            },
        )

        return {
            "workflow_id": workflow_id,
            "run_id": run_id,
            "result": {
                "phase": phase,
                "written_paths": written,
                "evidence_refs": evidence_refs,
                "cloudevent_ids": [started_id, completed_id],
                "ts": int(time.time()),
                "mode": self.mode,
            },
        }


# ---------------------------------------------------------------------
# singleton
orchestrator = KBOrchestrator()
