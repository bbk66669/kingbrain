# PR-1: Orchestrator API Testing Guide

This document provides instructions for testing the enhanced Orchestrator API functionality.

## Prerequisites

- Access to the KB environment
- `curl` command-line tool

## Basic Health Check

Verify the service is running:

```bash
curl http://kb.mwwnd.org/kb-api/health
```

Expected response:
```json
{"mode":"FAKE","status":"ok"}
```

## Configuration Endpoint

Check the current configuration:

```bash
curl http://kb.mwwnd.org/kb-api/config
```

Expected response (will vary based on environment):
```json
{
  "mode": "FAKE",
  "llm_providers_detected": [],
  "events_sink": "file",
  "repo_root": "/workspace",
  "deps": {
    "nats": {
      "available": true,
      "url": "nats://kb-nats.orchestrator.svc.cluster.local:4222"
    },
    "temporal": {
      "available": true,
      "url": "temporal-frontend.orchestrator.svc.cluster.local:7233"
    }
  }
}
```

Note: The response header `x-kb-mode` should also contain the current mode.

## Plan Workflow

Test the plan workflow with a simple request:

```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"task":"test task","notes":"testing the API"}' \
  http://kb.mwwnd.org/kb-api/plan
```

Expected response (workflow_id and other values will be different):
```json
{
  "workflow_id": "12345678-1234-5678-1234-567812345678",
  "run_id": "87654321-8765-4321-8765-432187654321",
  "result": {
    "phase": "PLAN",
    "written_paths": [
      "/workspace/docs/kingbrain/PLAN/result-12345678.md",
      "/workspace/.collab/PLAN/manifest-12345678.json"
    ],
    "evidence_refs": [
      "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "sbom:12345678-1234-5678-1234-567812345678"
    ],
    "cloudevent_ids": [
      "event-id-1",
      "event-id-2"
    ],
    "ts": 1629123456,
    "mode": "FAKE"
  }
}
```

## Path Allowlist Validation

Test the path allowlist validation by attempting to write to a forbidden path:

```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"task":"test task","notes":"testing path validation","paths_to_write":["/workspace/.git/config"]}' \
  http://kb.mwwnd.org/kb-api/plan
```

Expected response:
```json
{
  "error": "path not allowed",
  "detail": "Path /.git/config matches deny pattern: /.git/**",
  "workflow_id": "12345678-1234-5678-1234-567812345678",
  "event_id": "event-id-3"
}
```

## Other Workflow Endpoints

Test the other workflow endpoints:

```bash
# ACK workflow
curl -X POST -H "Content-Type: application/json" \
  -d '{"task":"test ack","notes":"testing ack","phase":"ACK"}' \
  http://kb.mwwnd.org/kb-api/ack

# BORROW workflow
curl -X POST -H "Content-Type: application/json" \
  -d '{"task":"test borrow","notes":"testing borrow","phase":"BORROW"}' \
  http://kb.mwwnd.org/kb-api/borrow

# DIFF workflow
curl -X POST -H "Content-Type: application/json" \
  -d '{"task":"test diff","notes":"testing diff","phase":"DIFF"}' \
  http://kb.mwwnd.org/kb-api/diff

# CR workflow
curl -X POST -H "Content-Type: application/json" \
  -d '{"task":"test cr","notes":"testing cr","phase":"CR"}' \
  http://kb.mwwnd.org/kb-api/cr
```

## Event Retrieval

After running a workflow, you can retrieve the generated events:

```bash
# Replace EVENT_ID with an actual event ID from a previous response
curl http://kb.mwwnd.org/kb-api/events/EVENT_ID
```

## Verifying CloudEvents

Check that events are being written to the local audit log:

```bash
# SSH into the pod
kubectl -n orchestrator exec -it deploy/kb-orchestrator -- /bin/bash

# Check the audit log directory
ls -la /workspace/.collab/audit/

# View the latest events file
cat /workspace/.collab/audit/events-YYYYMMDD.jsonl
```

The events should follow the CloudEvents format with the required fields.
