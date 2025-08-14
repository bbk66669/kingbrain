#!/usr/bin/env python3
"""
Local development runner for the KB Orchestrator
"""
import os
import sys
import logging

# Set up environment variables for local development
os.environ.setdefault("REPO_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("KB_MODE", "FAKE")

# Import after environment setup
from orchestrator.server import run_server

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"Starting KB Orchestrator in {os.environ.get('KB_MODE')} mode")
    print(f"Repo root: {os.environ.get('REPO_ROOT')}")
    run_server()
