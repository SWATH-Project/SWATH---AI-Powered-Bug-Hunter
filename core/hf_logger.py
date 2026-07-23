# core/hf_logger.py
# Author         : Member 3
# Responsibility : Handle SIEM-compatible JSONL event logging and terminal output.
# ------------------------------------------------------------

import os
import json
import time
from datetime import datetime
from loguru import logger
from core.siem_formatter import SIEMFormatter

class HFLogger:
    """
    Structured logger for SWATH.
    Writes machine-readable events to JSONL and formats human-readable
    logs to the terminal via Loguru.
    """

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        
        # Load tool fingerprints database
        # For this prototype we will load it directly if it exists
        self.fingerprints = {}
        target = os.path.join('data', 'tool_fingerprints.json')
        if os.path.exists(target):
            with open(target, 'r') as f:
                self.fingerprints = json.load(f)

        self.log_file = os.path.join(output_dir, 'logs', 'scan_events.jsonl')
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        
        # Determine format (could be read from a global config file later)
        self.siem_format = 'json'

    def _write_event(self, event: dict):
        event['timestamp'] = datetime.utcnow().isoformat() + "Z"
        
        # Apply SIEM Formatting
        formatted = SIEMFormatter.format(event, self.siem_format)
        
        with open(self.log_file, 'a') as f:
            f.write(formatted + '\n')

    def _enrich_tool(self, tool_name: str) -> dict:
        """Adds detection risk and visibility metrics to the log."""
        fp = self.fingerprints.get(tool_name, {})
        return {
            "detection_risk": fp.get("detection_risk", "unknown"),
            "defender_visibility": fp.get("defender_visibility", "unknown"),
            "category": fp.get("category", "unknown")
        }

    # ── Scan Level Events ────────────────────────────────────────

    def scan_start(self, domain: str):
        self._write_event({
            "event_type": "scan_start",
            "domain": domain,
            "status": "started"
        })

    def scan_end(self, final_tags: dict):
        self._write_event({
            "event_type": "scan_end",
            "status": "complete",
            "tags": list(final_tags.keys())
        })

    # ── Phase Level Events ───────────────────────────────────────

    def phase_start(self, phase_name: str, label: str):
        self._write_event({
            "event_type": "phase_start",
            "phase": phase_name,
            "label": label,
            "status": "started"
        })

    def phase_end(self, phase_name: str):
        self._write_event({
            "event_type": "phase_end",
            "phase": phase_name,
            "status": "complete"
        })

    # ── Tool Level Events ────────────────────────────────────────

    def tool_start(self, tool_name: str):
        event = {
            "event_type": "tool_start",
            "tool": tool_name,
            "status": "started"
        }
        event.update(self._enrich_tool(tool_name))
        self._write_event(event)

    def tool_complete(self, tool_name: str, result: dict, elapsed_seconds: float):
        """
        Log tool completion.

        Parameters:
            tool_name: Name of the tool
            result: Tool result dict containing 'count' and other metadata
            elapsed_seconds: Time taken to complete
        """
        findings_count = result.get('count', 0) if isinstance(result, dict) else (len(result) if hasattr(result, '__len__') else 0)
        event = {
            "event_type": "tool_complete",
            "tool": tool_name,
            "status": "success",
            "findings": findings_count,
            "elapsed_seconds": elapsed_seconds
        }
        event.update(self._enrich_tool(tool_name))
        self._write_event(event)

    def tool_skipped(self, tool_name: str, reason: str):
        event = {
            "event_type": "tool_skipped",
            "tool": tool_name,
            "status": "skipped",
            "reason": reason
        }
        self._write_event(event)

    def tool_error(self, tool_name: str, error):
        """
        Log tool error.

        Parameters:
            tool_name: Name of the tool
            error: Exception object or error string
        """
        if isinstance(error, Exception):
            error_str = str(error)
            error_type = error.__class__.__name__
        else:
            error_str = str(error)
            error_type = "Error"
        event = {
            "event_type": "tool_error",
            "tool": tool_name,
            "status": "failed",
            "error": error_str,
            "error_type": error_type
        }
        self._write_event(event)
