import os
import json
import time
from loguru import logger
from core.database import Database
from core.diff_engine import DiffEngine
from core.notifier import Notifier

class MonitorManager:
    """
    Continuous Monitoring System for SWATH.
    """
    
    def __init__(self, db: Database, notifier: Notifier = None):
        self.db = db
        self.notifier = notifier or Notifier()
        self.config_path = os.path.expanduser("~/.swath/monitors.json")
        self.monitors = self._load_monitors()

    def _load_monitors(self):
        if not os.path.exists(self.config_path):
            return {}
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading monitors: {e}")
            return {}

    def _save_monitors(self):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        try:
            with open(self.config_path, 'w') as f:
                json.dump(self.monitors, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving monitors: {e}")

    def add_monitor(self, domain: str, interval_hours: int = 24, phases: list = None, notify_channels: list = None):
        self.monitors[domain] = {
            'interval_hours': interval_hours,
            'phases': phases or [1, 3, 5, 7],
            'notify_channels': notify_channels or ['discord'],
            'last_run': 0
        }
        self._save_monitors()
        logger.info(f"Added monitor for {domain} every {interval_hours}h")

    def remove_monitor(self, domain: str):
        if domain in self.monitors:
            del self.monitors[domain]
            self._save_monitors()
            logger.info(f"Removed monitor for {domain}")

    def list_monitors(self) -> dict:
        return self.monitors

    def run_monitor_check(self, specific_domain: str = None):
        """Called by cron or scheduler to execute due monitors."""
        now = time.time()
        for domain, config in self.monitors.items():
            if specific_domain and specific_domain != "all" and specific_domain != domain:
                continue
                
            interval_sec = config['interval_hours'] * 3600
            if now - config['last_run'] >= interval_sec or specific_domain == domain:
                logger.info(f"Executing scheduled monitor scan for {domain}")
                
                from core.orchestrator_v2 import OrchestratorV2
                try:
                    orch = OrchestratorV2(domain)
                    scan_id = orch.run()
                    
                    # Update last run
                    self.monitors[domain]['last_run'] = now
                    self._save_monitors()
                    
                    # Generate diff
                    target_id = self.db.upsert_target(domain)
                    diff_engine = DiffEngine(self.db)
                    report = diff_engine.generate_diff_report(target_id, scan_id)
                    
                    if report != "No changes detected since last scan.":
                        self.notifier.notify(
                            'monitor_alert',
                            f"Attack Surface Change: {domain}",
                            report
                        )
                except Exception as e:
                    logger.error(f"Monitor scan failed for {domain}: {e}")

    def generate_cron_entry(self) -> str:
        """Returns the crontab line required to run the monitor automatically."""
        import sys
        swath_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'swath.py'))
        return f"0 * * * * {sys.executable} {swath_path} monitor run >> ~/.swath/monitor.log 2>&1"
