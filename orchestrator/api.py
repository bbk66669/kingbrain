import os
import json
import uuid
import time
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
import yaml

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("kb-orchestrator")

# Constants
REPO_ROOT = os.environ.get("REPO_ROOT", "/workspace")
KB_MODE = os.environ.get("KB_MODE", "AUTO")
NATS_URL = os.environ.get("NATS_URL", "nats://kb-nats.orchestrator.svc.cluster.local:4222")
TEMPORAL_URL = os.environ.get("TEMPORAL_URL", "temporal-frontend.orchestrator.svc.cluster.local:7233")

# CloudEvents constants
CLOUD_EVENT_SPEC_VERSION = "1.0"
CLOUD_EVENT_SOURCE = "kb-orchestrator"

# Path for audit logs
AUDIT_DIR = os.path.join(REPO_ROOT, ".collab/audit")
os.makedirs(AUDIT_DIR, exist_ok=True)

# Allowlist path
ALLOWLIST_PATH = os.path.join(REPO_ROOT, ".collab/paths.allowlist.yaml")

class KBOrchestrator:
    def __init__(self):
        self.mode = self._determine_mode()
        self.nats_client = None
        self.temporal_client = None
        
        # Try to initialize NATS client (non-blocking)
        self._init_nats()
        
        # Try to initialize Temporal client (non-blocking)
        self._init_temporal()
        
        # Load allowlist
        self.allowlist = self._load_allowlist()
        
        logger.info(f"KB Orchestrator initialized in {self.mode} mode")
    
    def _determine_mode(self) -> str:
        """Determine if we're in FAKE or REAL mode based on environment"""
        if KB_MODE.upper() == "FAKE":
            return "FAKE"
        elif KB_MODE.upper() == "REAL":
            return "REAL"
        
        # AUTO mode - check for LLM API keys
        has_openai = bool(os.environ.get("OPENAI_API_KEY"))
        has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
        has_azure = bool(os.environ.get("AZURE_OPENAI_KEY"))
        
        if has_openai or has_anthropic or has_azure:
            return "REAL"
        else:
            return "FAKE"
    
    def _init_nats(self):
        """Initialize NATS client (non-blocking)"""
        try:
            # Placeholder for actual NATS client initialization
            # We're not actually connecting in this PR, just simulating
            logger.info(f"NATS would connect to {NATS_URL}")
            self.nats_available = True
        except Exception as e:
            logger.warning(f"Failed to initialize NATS client: {e}")
            self.nats_available = False
    
    def _init_temporal(self):
        """Initialize Temporal client (non-blocking)"""
        try:
            # Placeholder for actual Temporal client initialization
            # We're not actually connecting in this PR, just simulating
            logger.info(f"Temporal would connect to {TEMPORAL_URL}")
            self.temporal_available = True
        except Exception as e:
            logger.warning(f"Failed to initialize Temporal client: {e}")
            self.temporal_available = False
    
    def _load_allowlist(self) -> Dict:
        """Load the allowlist configuration"""
        try:
            with open(ALLOWLIST_PATH, 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed to load allowlist: {e}")
            # Return a default restrictive allowlist
            return {"allow": [], "deny": ["**"], "writable": []}
    
    def _check_path_allowed(self, path: str) -> Tuple[bool, str]:
        """
        Check if a path is allowed according to allowlist rules
        Returns (allowed, reason)
        """
        # Strip /workspace prefix if present
        if path.startswith("/workspace"):
            path = path[len("/workspace"):]
        
        # Ensure path starts with /
        if not path.startswith("/"):
            path = "/" + path
        
        # Check deny list first (deny has priority)
        for pattern in self.allowlist.get("deny", []):
            if self._path_matches_pattern(path, pattern):
                return False, f"Path {path} matches deny pattern: {pattern}"
        
        # Check if path is both allowed and writable
        allowed = False
        for pattern in self.allowlist.get("allow", []):
            if self._path_matches_pattern(path, pattern):
                allowed = True
                break
        
        if not allowed:
            return False, f"Path {path} is not in allow list"
        
        writable = False
        for pattern in self.allowlist.get("writable", []):
            if self._path_matches_pattern(path, pattern):
                writable = True
                break
        
        if not writable:
            return False, f"Path {path} is not in writable list"
        
        return True, ""
    
    def _path_matches_pattern(self, path: str, pattern: str) -> bool:
        """Simple pattern matching for paths"""
        # Convert ** to proper regex
        if "**" in pattern:
            pattern = pattern.replace("**", ".*")
        # Convert * to proper regex (but not matching /)
        if "*" in pattern:
            pattern = pattern.replace("*", "[^/]*")
        
        # Escape other regex special chars
        import re
        pattern = re.escape(pattern).replace("\\[", "[").replace("\\]", "]").replace("\\.\\*", ".*").replace("\\[\\^/\\]\\*", "[^/]*")
        
        # Add start/end anchors
        if not pattern.endswith(".*"):
            pattern += "$"
        
        return bool(re.match(pattern, path))
    
    def _get_audit_file_path(self) -> str:
        """Get the path to today's audit log file"""
        today = datetime.now().strftime("%Y%m%d")
        return os.path.join(AUDIT_DIR, f"events-{today}.jsonl")
    
    def _write_cloud_event(self, event_type: str, workflow_id: str, data: Dict) -> str:
        """
        Write a CloudEvent to NATS and local audit log
        Returns the event ID
        """
        event_id = str(uuid.uuid4())
        
        cloud_event = {
            "id": event_id,
            "source": CLOUD_EVENT_SOURCE,
            "specversion": CLOUD_EVENT_SPEC_VERSION,
            "type": event_type,
            "subject": workflow_id,
            "time": datetime.utcnow().isoformat() + "Z",
            "data": data
        }
        
        # Try to publish to NATS (if available)
        if self.nats_available:
            try:
                # Placeholder for actual NATS publish
                logger.info(f"Would publish to NATS: {event_type}")
            except Exception as e:
                logger.warning(f"Failed to publish to NATS: {e}")
        
        # Always write to local audit log (even if NATS fails)
        try:
            audit_file = self._get_audit_file_path()
            os.makedirs(os.path.dirname(audit_file), exist_ok=True)
            with open(audit_file, 'a') as f:
                f.write(json.dumps(cloud_event) + "\n")
        except Exception as e:
            logger.error(f"Failed to write to audit log: {e}")
        
        return event_id
    
    def get_config(self) -> Dict:
        """Get the current configuration"""
        llm_providers = []
        if os.environ.get("OPENAI_API_KEY"):
            llm_providers.append("openai")
        if os.environ.get("ANTHROPIC_API_KEY"):
            llm_providers.append("anthropic")
        if os.environ.get("AZURE_OPENAI_KEY"):
            llm_providers.append("azure")
        
        return {
            "mode": self.mode,
            "llm_providers_detected": llm_providers,
            "events_sink": "nats+file" if self.nats_available else "file",
            "repo_root": REPO_ROOT,
            "deps": {
                "nats": {
                    "available": self.nats_available,
                    "url": NATS_URL
                },
                "temporal": {
                    "available": self.temporal_available,
                    "url": TEMPORAL_URL
                }
            }
        }
    
    def get_event(self, event_id: str) -> Optional[Dict]:
        """Retrieve a specific event from the audit log"""
        try:
            # Check all audit files, starting with today
            today = datetime.now().strftime("%Y%m%d")
            audit_files = [f for f in os.listdir(AUDIT_DIR) if f.startswith("events-")]
            
            # Sort in reverse chronological order
            audit_files.sort(reverse=True)
            
            for file_name in audit_files:
                file_path = os.path.join(AUDIT_DIR, file_name)
                with open(file_path, 'r') as f:
                    for line in f:
                        try:
                            event = json.loads(line)
                            if event.get("id") == event_id:
                                return event
                        except json.JSONDecodeError:
                            continue
            
            return None
        except Exception as e:
            logger.error(f"Error retrieving event {event_id}: {e}")
            return None
    
    def process_workflow(self, task: str, notes: str, phase: str, 
                         paths_to_write: List[str] = None) -> Dict:
        """
        Process a workflow request
        Returns the workflow result or error
        """
        workflow_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        
        # Check if paths are allowed
        if paths_to_write:
            for path in paths_to_write:
                allowed, reason = self._check_path_allowed(path)
                if not allowed:
                    # Path not allowed, emit rejected event
                    event_type = f"kb.workflow.{phase}.rejected.v1"
                    event_data = {
                        "phase": phase,
                        "mode": self.mode,
                        "reason": reason
                    }
                    event_id = self._write_cloud_event(event_type, workflow_id, event_data)
                    
                    return {
                        "error": "path not allowed",
                        "detail": reason,
                        "workflow_id": workflow_id,
                        "event_id": event_id
                    }
        
        # Emit started event
        started_event_type = f"kb.workflow.{phase}.started.v1"
        started_event_data = {
            "phase": phase,
            "mode": self.mode,
            "task": task,
            "notes": notes
        }
        started_event_id = self._write_cloud_event(started_event_type, workflow_id, started_event_data)
        
        # In FAKE mode, we just simulate a successful workflow
        # In REAL mode, we would actually execute the workflow via Temporal
        
        # Simulate some processing time
        time.sleep(0.1)
        
        # Example paths that would be written in a real workflow
        written_paths = paths_to_write or [
            f"{REPO_ROOT}/docs/kingbrain/{phase}/result-{workflow_id[:8]}.md",
            f"{REPO_ROOT}/.collab/{phase}/manifest-{workflow_id[:8]}.json"
        ]
        
        # Example evidence references
        evidence_refs = [
            f"sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            f"sbom:{workflow_id}"
        ]
        
        # Emit completed event
        completed_event_type = f"kb.workflow.{phase}.completed.v1"
        completed_event_data = {
            "phase": phase,
            "mode": self.mode,
            "written": written_paths,
            "evidenceRefs": evidence_refs
        }
        completed_event_id = self._write_cloud_event(completed_event_type, workflow_id, completed_event_data)
        
        # Return the result
        return {
            "workflow_id": workflow_id,
            "run_id": run_id,
            "result": {
                "phase": phase,
                "written_paths": written_paths,
                "evidence_refs": evidence_refs,
                "cloudevent_ids": [started_event_id, completed_event_id],
                "ts": int(time.time()),
                "mode": self.mode
            }
        }

# Create a singleton instance
orchestrator = KBOrchestrator()
