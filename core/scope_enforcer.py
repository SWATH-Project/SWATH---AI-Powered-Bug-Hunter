# core/scope_enforcer.py
# Author         : Member 4
# Responsibility : Enforce bug bounty scope limits strictly.
# ------------------------------------------------------------

import os
import re
import json
from loguru import logger
from core.exceptions import OutOfScopeError

class ScopeEnforcer:
    def __init__(self, config_dir: str = "~/.swath"):
        self.config_dir = os.path.expanduser(config_dir)
        self.scope_file = os.path.join(self.config_dir, 'scope.json')
        self._load_scopes()

    def _load_scopes(self):
        """Loads allowed scopes from JSON. If it doesn't exist or is corrupted, recreate it."""
        if not os.path.exists(self.scope_file):
            os.makedirs(self.config_dir, exist_ok=True)
            with open(self.scope_file, 'w') as f:
                json.dump({
                    "programs": {
                        "Example Program": {
                            "in_scope": ["*.example.com", "example.com"],
                            "out_of_scope": ["admin.example.com"]
                        }
                    }
                }, f, indent=4)

        try:
            with open(self.scope_file, 'r') as f:
                self.scopes = json.load(f).get("programs", {})
        except json.JSONDecodeError as e:
            # Backup corrupted file and recreate default
            import datetime
            backup_path = f"{self.scope_file}.corrupt.{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
            os.rename(self.scope_file, backup_path)
            logger.warning(f"Scope file corrupted (JSON error: {e}). Backed up to {backup_path}")
            # Create fresh default
            os.makedirs(self.config_dir, exist_ok=True)
            with open(self.scope_file, 'w') as f:
                json.dump({
                    "programs": {
                        "Example Program": {
                            "in_scope": ["*.example.com", "example.com"],
                            "out_of_scope": ["admin.example.com"]
                        }
                    }
                }, f, indent=4)
            self.scopes = {"Example Program": {
                "in_scope": ["*.example.com", "example.com"],
                "out_of_scope": ["admin.example.com"]
            }}
            logger.info("Created new default scope file. Please edit ~/.swath/scope.json with your targets.")

    def _match(self, domain: str, pattern: str) -> bool:
        """Evaluates wildcard patterns like *.example.com against the domain."""
        if pattern.startswith("*."):
            base_domain = pattern[2:]
            return domain == base_domain or domain.endswith("." + base_domain)
        return domain == pattern

    def check(self, domain: str) -> tuple[bool, str, str]:
        """
        Check if the domain is explicitly allowed in the scope file.
        Returns: (is_allowed, reason, program_name)
        """
        for program_name, rules in self.scopes.items():
            in_scope = rules.get("in_scope", [])
            out_of_scope = rules.get("out_of_scope", [])
            
            # 1. Check strict exclusions first
            for out_pattern in out_of_scope:
                if self._match(domain, out_pattern):
                    return False, f"Matches explicit out-of-scope rule: {out_pattern}", program_name
                    
            # 2. Check inclusions
            for in_pattern in in_scope:
                if self._match(domain, in_pattern):
                    return True, f"Matches in-scope rule: {in_pattern}", program_name
                    
        return False, "Domain does not match any known program scope.", "Unknown Program"

    def approve_manual(self, domain: str, confirm: str) -> bool:
        """Manually approve a domain during runtime."""
        if confirm.strip() == domain:
            # Optionally add to scope.json here for persistence
            if "Manual Approvals" not in self.scopes:
                self.scopes["Manual Approvals"] = {"in_scope": [], "out_of_scope": []}
            if domain not in self.scopes["Manual Approvals"]["in_scope"]:
                self.scopes["Manual Approvals"]["in_scope"].append(domain)
                
                with open(self.scope_file, 'w') as f:
                    json.dump({"programs": self.scopes}, f, indent=4)
                    
            return True
        return False
