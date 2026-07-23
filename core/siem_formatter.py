# core/siem_formatter.py
# Author         : Member 3
# Responsibility : Converts standard SWATH event dicts into SIEM-compatible
#                  formats (Splunk JSON, CEF, LEEF) for enterprise integration.
# ------------------------------------------------------------

import json

class SIEMFormatter:
    """
    Formats hunt events for consumption by external logging systems.
    """

    @staticmethod
    def format(event: dict, format_type: str = "json") -> str:
        if format_type == "cef":
            return SIEMFormatter.to_cef(event)
        elif format_type == "leef":
            return SIEMFormatter.to_leef(event)
        else:
            # Default structured JSON (Splunk/Elastic compatible)
            return json.dumps(event)

    @staticmethod
    def to_cef(event: dict) -> str:
        """
        Micro Focus ArcSight Common Event Format (CEF).
        CEF:Version|Device Vendor|Device Product|Device Version|Signature ID|Name|Severity|Extension
        """
        vendor = "SWATH"
        product = "BugBountyFramework"
        version = "3.0"
        sig_id = event.get("event_type", "unknown")
        name = event.get("tool", event.get("phase", "Orchestrator Event"))
        
        # Map our status to CEF Severity (0-10)
        sev_map = {"start": 3, "complete": 5, "error": 8, "skipped": 2}
        severity = sev_map.get(event.get("status"), 1)
        
        extensions = []
        for k, v in event.items():
            if k not in ["event_type", "status", "timestamp"]:
                extensions.append(f"{k}={v}")
                
        ext_str = " ".join(extensions)
        return f"CEF:0|{vendor}|{product}|{version}|{sig_id}|{name}|{severity}|{ext_str}"

    @staticmethod
    def to_leef(event: dict) -> str:
        """
        IBM QRadar Log Event Extended Format (LEEF).
        LEEF:Version|Vendor|Product|Version|EventID|DelimiterCharacter|Extension
        """
        vendor = "SWATH"
        product = "BugBountyFramework"
        version = "3.0"
        event_id = event.get("event_type", "unknown")
        
        extensions = []
        for k, v in event.items():
            if k not in ["event_type", "timestamp"]:
                extensions.append(f"{k}={v}")
                
        ext_str = "\t".join(extensions)
        return f"LEEF:1.0|{vendor}|{product}|{version}|{event_id}|\t|{ext_str}"
