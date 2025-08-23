import os
import json
import uuid
import time
import logging
import base64
import hashlib
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
import yaml
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum

# ---------------- Logging ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("kb-orchestrator")

# ---------------- Constants ----------------
REPO_ROOT = os.environ.get("REPO_ROOT", "/workspace")
KB_MODE = os.environ.get("KB_MODE", "AUTO")
NATS_URL = os.environ.get("NATS_URL", "nats://kb-nats.orchestrator.svc.cluster.local:4222")
TEMPORAL_URL = os.environ.get("TEMPORAL_URL", "temporal-frontend.orchestrator.svc.cluster.local:7233")

# CloudEvents constants
CLOUD_EVENT_SPEC_VERSION = "1.0"
CLOUD_EVENT_SOURCE = "kb-orchestrator"

# Paths
AUDIT_DIR = os.path.join(REPO_ROOT, ".collab/audit")
os.makedirs(AUDIT_DIR, exist_ok=True)
ALLOWLIST_PATH = os.path.join(REPO_ROOT, ".collab/paths.allowlist.yaml")


# ---------------- Types ----------------
class KBMode(Enum):
    FAKE = "FAKE"
    REAL = "REAL"
    AUTO = "AUTO"


@dataclass
class WorkflowResult:
    workflow_id: str
    run_id: str
    phase: str
    written_paths: List[str]
    evidence_refs: List[str]
    cloudevent_ids: List[str]
    timestamp: int
    mode: str
    error: Optional[str] = None


@dataclass
class PathValidationResult:
    allowed: bool
    reason: str = ""


class KBOrchestratorError(Exception):
    """Base exception for KB Orchestrator errors"""
    pass


class PathNotAllowedError(KBOrchestratorError):
    """Raised when a path is not allowed by the allowlist"""
    pass


class ComposerError(KBOrchestratorError):
    """Raised when composer execution fails"""
    pass


# ---------------- Orchestrator ----------------
class KBOrchestrator:
    def __init__(self):
        self.mode = self._determine_mode()
        self.nats_client = None
        self.temporal_client = None

        # Try to initialize deps (non-blocking)
        self._init_nats()
        self._init_temporal()

        # Load allowlist
        self.allowlist = self._load_allowlist()

        logger.info(f"KB Orchestrator initialized in {self.mode.value} mode")

    # ---- mode / deps -----------------------------------------------------
    def _determine_mode(self) -> KBMode:
        kb_mode = (KB_MODE or "AUTO").upper()
        if kb_mode == "FAKE":
            return KBMode.FAKE
        if kb_mode == "REAL":
            return KBMode.REAL
        # AUTO -> detect keys
        has_openai = bool(os.environ.get("OPENAI_API_KEY"))
        has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
        has_azure = bool(os.environ.get("AZURE_OPENAI_KEY"))
        return KBMode.REAL if (has_openai or has_anthropic or has_azure) else KBMode.FAKE

    def _init_nats(self):
        try:
            logger.info(f"NATS would connect to {NATS_URL}")
            self.nats_available = True
        except Exception as e:
            logger.warning(f"Failed to initialize NATS client: {e}")
            self.nats_available = False

    def _init_temporal(self):
        try:
            logger.info(f"Temporal would connect to {TEMPORAL_URL}")
            self.temporal_available = True
        except Exception as e:
            logger.warning(f"Failed to initialize Temporal client: {e}")
            self.temporal_available = False

    # ---- allowlist / path checks ----------------------------------------
    def _load_allowlist(self) -> Dict:
        """Load the allowlist configuration with better error handling"""
        default_allowlist = {
            "allow": ["/.collab/**", "/docs/**", "/reports/**"],
            "deny": ["/**/.git/**", "/**/node_modules/**"],
            "writable": ["/.collab/**", "/docs/**", "/reports/**"],
        }
        try:
            p = Path(ALLOWLIST_PATH)
            if not p.exists():
                logger.warning(f"Allowlist file not found at {ALLOWLIST_PATH}, using defaults")
                return default_allowlist
            with p.open("r") as f:
                data = yaml.safe_load(f) or {}
            for key in ("allow", "deny", "writable"):
                if key not in data or data[key] is None:
                    logger.warning(f"Missing '{key}' in allowlist, using default empty list")
                    data[key] = []
            # Normalize entries to always start with "/"
            def _norm(globs: List[str]) -> List[str]:
                out = []
                for g in globs:
                    if not g:
                        continue
                    out.append(g if g.startswith("/") else "/" + g)
                return out
            data["allow"] = _norm(data.get("allow", []))
            data["deny"] = _norm(data.get("deny", []))
            data["writable"] = _norm(data.get("writable", []))
            return data
        except Exception as e:
            logger.error(f"Failed to load allowlist: {e}, using defaults")
            return default_allowlist

    def _normalize_path(self, path: str) -> str:
        """Normalize path for consistent checking"""
        if path.startswith("/workspace"):
            path = path[len("/workspace"):]
        if path.startswith(REPO_ROOT):
            path = path[len(REPO_ROOT):]
        if not path.startswith("/"):
            path = "/" + path
        # strip trailing slash (except root)
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
        return path

    def _path_matches_pattern(self, path: str, pattern: str) -> bool:
        """Glob-like pattern matching using regex (^...$)"""
        import re
        # Convert ** and * to regex
        pat = pattern.replace("**", "DOUBLE_STAR").replace("*", "SINGLE_STAR")
        pat = re.escape(pat)
        pat = pat.replace("DOUBLE_STAR", ".*").replace("SINGLE_STAR", "[^/]*")
        regex = f"^{pat}$"
        return bool(re.match(regex, path))

    def _check_path_allowed(self, path: str) -> PathValidationResult:
        normalized_path = self._normalize_path(path)

        # deny first
        for pat in self.allowlist.get("deny", []):
            if self._path_matches_pattern(normalized_path, pat):
                return PathValidationResult(False, f"Path '{normalized_path}' matches deny pattern: '{pat}'")

        # allow
        allow_patterns = self.allowlist.get("allow", [])
        if not allow_patterns:
            return PathValidationResult(False, "No allow patterns defined")
        if not any(self._path_matches_pattern(normalized_path, pat) for pat in allow_patterns):
            return PathValidationResult(False, f"Path '{normalized_path}' does not match any allow pattern: {allow_patterns}")

        # writable
        writable_patterns = self.allowlist.get("writable", [])
        if not writable_patterns:
            return PathValidationResult(False, "No writable patterns defined")
        if not any(self._path_matches_pattern(normalized_path, pat) for pat in writable_patterns):
            return PathValidationResult(False, f"Path '{normalized_path}' is not in writable patterns: {writable_patterns}")

        return PathValidationResult(True)

    # ---- audit / events --------------------------------------------------
    def _get_audit_file_path(self) -> str:
        today = datetime.now().strftime("%Y%m%d")
        return os.path.join(AUDIT_DIR, f"events-{today}.jsonl")

    @contextmanager
    def _audit_transaction(self, workflow_id: str):
        """Context manager for audit transaction integrity"""
        try:
            yield
        except Exception as e:
            self._write_cloud_event(
                "kb.workflow.rollback.v1",
                workflow_id,
                {"error": str(e), "timestamp": datetime.utcnow().isoformat()}
            )
            raise

    def _write_cloud_event(self, event_type: str, workflow_id: str, data: Dict) -> str:
        event_id = str(uuid.uuid4())
        cloud_event = {
            "id": event_id,
            "source": CLOUD_EVENT_SOURCE,
            "specversion": CLOUD_EVENT_SPEC_VERSION,
            "type": event_type,
            "subject": workflow_id,
            "time": datetime.utcnow().isoformat() + "Z",
            "data": data,
            "datacontenttype": "application/json",
        }

        # best-effort NATS
        if getattr(self, "nats_available", False):
            try:
                logger.info(f"Would publish to NATS: {event_type} ({event_id})")
            except Exception as e:
                logger.warning(f"NATS publish failed: {e}")

        # append to file with simple retry
        audit_file = self._get_audit_file_path()
        Path(audit_file).parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(3):
            try:
                tmp = f"{audit_file}.tmp.{event_id}"
                with open(tmp, "w") as f:
                    f.write(json.dumps(cloud_event) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                with open(audit_file, "a") as out, open(tmp, "r") as inp:
                    out.write(inp.read())
                    out.flush()
                    os.fsync(out.fileno())
                os.remove(tmp)
                break
            except Exception as e:
                if attempt == 2:
                    logger.error(f"Failed to write audit after retries: {e}")
                else:
                    logger.warning(f"Audit write failed (attempt {attempt+1}): {e}")
                    time.sleep(0.1 * (attempt + 1))
        return event_id

    def get_event(self, event_id: str) -> Optional[Dict]:
        try:
            files = [f for f in os.listdir(AUDIT_DIR) if f.startswith("events-")]
            files.sort(reverse=True)
            for name in files:
                with open(os.path.join(AUDIT_DIR, name), "r") as fh:
                    for line_num, line in enumerate(fh, 1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError as je:
                            logger.warning(f"Invalid JSON in {name}:{line_num}: {je}")
                            continue
                        if ev.get("id") == event_id:
                            return ev
            return None
        except Exception as e:
            logger.error(f"Error retrieving event {event_id}: {e}")
            return None

    # ---- file IO helpers -------------------------------------------------
    def _get_default_paths(self, phase: str, workflow_id: str) -> List[str]:
        base = Path(REPO_ROOT)
        sid = workflow_id[:8]
        if phase in ("ACK", "PLAN", "BORROW", "DIFF"):
            # Write both files under /docs/kingbrain/<PHASE>/..., keeping manifest inside the docs tree
            return [
                str(base / "docs" / "kingbrain" / phase / f"result-{sid}.md"),
                str(base / "docs" / "kingbrain" / phase / f"manifest-{sid}.json"),
            ]
        # default (e.g. CR -> reports unless allowlist已放开 docs/kingbrain/CR/**)
        return [
            str(base / "reports" / f"{phase}-result-{sid}.md"),
            str(base / "reports" / f"{phase}-manifest-{sid}.json"),
        ]

    def _write_bytes_safe(self, path: str, content: bytes) -> str:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + f".tmp.{uuid.uuid4().hex[:6]}")
        with open(tmp, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        tmp.rename(p)
        return "sha256:" + hashlib.sha256(content).hexdigest()

    def _apply_artifacts(self,
                         artifacts: List[Dict[str, Any]],
                         written_paths: List[str],
                         evidence_refs: List[str]) -> None:
        if not artifacts:
            return
        for i, artifact in enumerate(artifacts):
            try:
                relpath = artifact.get("relpath")
                if not relpath:
                    logger.warning(f"Artifact {i} missing relpath, skipping")
                    continue
                abs_path = relpath if os.path.isabs(relpath) else os.path.join(REPO_ROOT, relpath)
                validation = self._check_path_allowed(abs_path)
                if not validation.allowed:
                    logger.warning(f"Artifact {i} rejected: {abs_path} ({validation.reason})")
                    continue
                raw = artifact.get("content", b"")
                enc = (artifact.get("encoding") or "utf-8").lower()
                if isinstance(raw, bytes):
                    content_bytes = raw
                elif enc == "base64":
                    try:
                        content_bytes = base64.b64decode(raw)
                    except Exception as e:
                        logger.error(f"Artifact {i} base64 decode failed: {e}")
                        continue
                else:
                    content_bytes = str(raw).encode("utf-8")
                digest = self._write_bytes_safe(abs_path, content_bytes)
                written_paths.append(abs_path)
                evidence_refs.append(digest)
                logger.info(f"Applied artifact {i}: {abs_path} ({len(content_bytes)} bytes)")
            except Exception as e:
                logger.error(f"Failed to apply artifact {i}: {e}")

    def _create_placeholder_files(self,
                                  candidate_paths: List[str],
                                  workflow_id: str,
                                  run_id: str,
                                  phase: str,
                                  task: str,
                                  notes: str,
                                  written_paths: List[str],
                                  evidence_refs: List[str]) -> None:
        for path in candidate_paths:
            try:
                if path.endswith(".json"):
                    payload = {
                        "phase": phase,
                        "workflow_id": workflow_id,
                        "run_id": run_id,
                        "mode": self.mode.value,
                        "task": task,
                        "notes": notes,
                        "timestamp": int(time.time()),
                        "placeholder": True,
                    }
                    content = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
                else:
                    content = (
                        f"# {phase} Placeholder\n\n"
                        f"- workflow_id: {workflow_id}\n"
                        f"- run_id: {run_id}\n"
                        f"- mode: {self.mode.value}\n"
                        f"- phase: {phase}\n"
                        f"- ts: {int(time.time())}\n\n"
                        f"## Task\n{task}\n\n## Notes\n{notes}\n"
                    ).encode("utf-8")
                digest = self._write_bytes_safe(path, content)
                written_paths.append(path)
                evidence_refs.append(digest)
            except Exception as e:
                logger.error(f"Failed to create placeholder file {path}: {e}")

    # ---- public API ------------------------------------------------------
    def get_config(self) -> Dict:
        llm_providers = []
        if os.environ.get("OPENAI_API_KEY"):
            llm_providers.append("openai")
        if os.environ.get("ANTHROPIC_API_KEY"):
            llm_providers.append("anthropic")
        if os.environ.get("AZURE_OPENAI_KEY"):
            llm_providers.append("azure")
        return {
            "mode": self.mode.value,
            "llm_providers_detected": llm_providers,
            "events_sink": "nats+file" if getattr(self, "nats_available", False) else "file",
            "repo_root": REPO_ROOT,
            "allowlist_path": ALLOWLIST_PATH,
            "allowlist_loaded": bool(self.allowlist),
            "audit_dir": AUDIT_DIR,
            "deps": {
                "nats": {"available": getattr(self, "nats_available", False), "url": NATS_URL},
                "temporal": {"available": getattr(self, "temporal_available", False), "url": TEMPORAL_URL},
            },
        }

    def validate_paths(self, paths: List[str]) -> Dict[str, PathValidationResult]:
        return {p: self._check_path_allowed(p) for p in paths}

    def process_workflow(self,
                         task: str,
                         notes: str,
                         phase: str,
                         paths_to_write: Optional[List[str]] = None) -> WorkflowResult:
        """
        主流程：校验→事件→(REAL) 调 Composer 产物并真写 / (FAKE) 占位→事件
        """
        workflow_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())

        try:
            with self._audit_transaction(workflow_id):
                candidate_paths = paths_to_write or self._get_default_paths(phase, workflow_id)

                # upfront validation
                validations = self.validate_paths(candidate_paths)
                invalid = [(p, r.reason) for p, r in validations.items() if not r.allowed]
                if invalid:
                    eid = self._write_cloud_event(
                        f"kb.workflow.{phase}.rejected.v1",
                        workflow_id,
                        {"phase": phase, "mode": self.mode.value, "invalid_paths": invalid}
                    )
                    return WorkflowResult(
                        workflow_id=workflow_id,
                        run_id=run_id,
                        phase=phase,
                        written_paths=[],
                        evidence_refs=[],
                        cloudevent_ids=[eid],
                        timestamp=int(time.time()),
                        mode=self.mode.value,
                        error="; ".join([f"{p}: {why}" for p, why in invalid])
                    )

                started_id = self._write_cloud_event(
                    f"kb.workflow.{phase}.started.v1",
                    workflow_id,
                    {"phase": phase, "mode": self.mode.value, "task": task, "notes": notes, "candidate_paths": candidate_paths}
                )

                time.sleep(0.05)  # simulate small processing

                written_paths: List[str] = []
                evidence_refs: List[str] = []
                artifacts: List[Dict[str, Any]] = []

                if self.mode == KBMode.REAL:
                    try:
                        from services.composer import run_graph
                        result = run_graph(
                            phase=phase,
                            task=task,
                            notes=notes,
                            context={
                                "repo_root": REPO_ROOT,
                                "agents_file": os.path.join(REPO_ROOT, ".collab/agents.yaml"),
                                "workflow_id": workflow_id,
                                "run_id": run_id,
                            },
                        )
                        if isinstance(result, dict):
                            artifacts = result.get("artifacts", [])
                    except ImportError:
                        logger.warning("Composer service not available, falling back to placeholders")
                    except Exception as e:
                        logger.warning(f"Composer execution failed: {e}; falling back to placeholders")

                if artifacts:
                    self._apply_artifacts(artifacts, written_paths, evidence_refs)
                else:
                    self._create_placeholder_files(
                        candidate_paths, workflow_id, run_id, phase, task, notes,
                        written_paths, evidence_refs
                    )

                evidence_refs.append(f"sbom:{workflow_id}")

                completed_id = self._write_cloud_event(
                    f"kb.workflow.{phase}.completed.v1",
                    workflow_id,
                    {"phase": phase, "mode": self.mode.value,
                     "written_paths": written_paths, "evidence_refs": evidence_refs,
                     "composer_artifacts_count": len(artifacts)}
                )

                return WorkflowResult(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    phase=phase,
                    written_paths=written_paths,
                    evidence_refs=evidence_refs,
                    cloudevent_ids=[started_id, completed_id],
                    timestamp=int(time.time()),
                    mode=self.mode.value
                )
        except Exception as e:
            logger.error(f"Workflow processing failed: {e}")
            fail_id = self._write_cloud_event(
                f"kb.workflow.{phase}.failed.v1",
                workflow_id,
                {"phase": phase, "mode": self.mode.value, "error": str(e)}
            )
            return WorkflowResult(
                workflow_id=workflow_id,
                run_id=run_id,
                phase=phase,
                written_paths=[],
                evidence_refs=[],
                cloudevent_ids=[fail_id],
                timestamp=int(time.time()),
                mode=self.mode.value,
                error=str(e)
            )


# Singleton
orchestrator = KBOrchestrator()
