# core/budget_tracker.py
# Author         : Member 1
# Responsibility : Track total requests and time elapsed.
#                  Fails the execution if budget is exceeded.
# ------------------------------------------------------------

import json
import os
import time
from datetime import datetime
from typing import Optional
from core.exceptions import BudgetExceededError

class BudgetTracker:
    """
    Tracks execution time and HTTP request budget during a scan.
    """

    def __init__(self, max_requests: Optional[int] = None, max_time_minutes: Optional[int] = None):
        self.max_requests = max_requests
        self.max_time_minutes = max_time_minutes

        self.requests_used = 0
        self.start_time = time.time()
        import threading
        self.lock = threading.Lock()

    def add_requests(self, count: int):
        """Add executed requests to the budget tracker."""
        with self.lock:
            if count and count > 0:
                self.requests_used += count

    def within_limits(self, estimated_requests: int = 0) -> bool:
        """
        Check if we are within budget and can afford the estimated_requests.
        Called by Orchestrator Gate 2.

        Returns True if within budget, False if adding estimated_requests would blow it.
        Or throws BudgetExceededError if already blown.
        """
        # Time check
        if self.max_time_minutes:
            elapsed_mins = (time.time() - self.start_time) / 60
            if elapsed_mins > self.max_time_minutes:
                raise BudgetExceededError(
                    f"Scan elapsed time ({elapsed_mins:.1f}m) exceeded limit ({self.max_time_minutes}m)"
                )

        # Request check
        with self.lock:
            if self.max_requests:
                if self.requests_used >= self.max_requests:
                    raise BudgetExceededError(
                        f"Scan request budget exhausted: {self.requests_used}/{self.max_requests} requests used."
                    )

                # Check if this tool would blow the budget
                if self.requests_used + estimated_requests > self.max_requests:
                    return False

        return True

    def log_status(self) -> dict:
        """Returns the current budget status for reporting."""
        with self.lock:
            elapsed_mins = (time.time() - self.start_time) / 60
            return {
                "requests_used": self.requests_used,
                "max_requests": self.max_requests,
                "elapsed_minutes": round(elapsed_mins, 2),
                "max_time_minutes": self.max_time_minutes,
                "time_budget_remaining_mins": self.max_time_minutes - elapsed_mins if self.max_time_minutes else None,
                "request_budget_remaining": self.max_requests - self.requests_used if self.max_requests else None
            }

    def get_status(self) -> dict:
        """Returns a dashboard-friendly status dict."""
        with self.lock:
            elapsed_seconds = time.time() - self.start_time
            elapsed_minutes = elapsed_seconds / 60
            remaining = (self.max_requests - self.requests_used) if self.max_requests else None
            percent_used = (self.requests_used / self.max_requests * 100) if self.max_requests else None
            return {
                "requests_used": self.requests_used,
                "max_requests": self.max_requests,
                "remaining_requests": remaining,
                "elapsed_seconds": round(elapsed_seconds, 2),
                "elapsed_minutes": round(elapsed_minutes, 2),
                "percent_used": round(percent_used, 2) if percent_used is not None else None,
            }

    def save_to_file(self, output_dir: str):
        """Writes budget status to output/<domain>/processed/budget_status.json."""
        processed_dir = os.path.join(output_dir, "processed")
        os.makedirs(processed_dir, exist_ok=True)
        path = os.path.join(processed_dir, "budget_status.json")
        status = self.get_status()
        status["timestamp"] = datetime.utcnow().isoformat()
        with open(path, "w") as f:
            json.dump(status, f, indent=2)

    def reset(self):
        """Resets request count and timer for resuming scans."""
        with self.lock:
            self.requests_used = 0
            self.start_time = time.time()

    def __repr__(self) -> str:
        s = f"BudgetTracker(requests: {self.requests_used}"
        if self.max_requests:
            s += f"/{self.max_requests}"
        elapsed = (time.time() - self.start_time) / 60
        s += f", time: {elapsed:.1f}m"
        if self.max_time_minutes:
            s += f"/{self.max_time_minutes}m"
        s += ")"
        return s
