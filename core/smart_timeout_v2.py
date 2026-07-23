# core/smart_timeout_v2.py
# Author         : SWATH Core Team
# Responsibility : Production-grade activity-aware process monitoring.
#                  Uses hybrid detection (Output growth + CPU/IO activity)
#                  and graceful SIGTERM→SIGKILL termination.
# ------------------------------------------------------------

import os
import time
import threading
import subprocess
from typing import Optional, List, Dict, Any
import psutil
from loguru import logger
from core.exceptions import ToolTimeoutError, ToolExecutionError


class SmartTimeoutV2:
    """
    Advanced process runner with hybrid activity monitoring.

    Decision Logic per timeout window:
    1. If output file grows OR stdout is produced → EXTEND
    2. Else if process shows CPU or IO delta > threshold → EXTEND
    3. Else (completely silent and idle) → TERMINATE

    Enforces a hard ceiling via max_extensions to prevent runaway processes.
    """

    # ── Defaults ─────────────────────────────────────────────────
    DEFAULT_TIMEOUT = 300
    MAX_EXTENSIONS = 10
    EXTENSION_SECONDS = 300
    MONITOR_INTERVAL = 15
    ACTIVITY_THRESHOLD_BYTES = 10240   # 10 KB IO delta
    ACTIVITY_THRESHOLD_CPU = 0.5       # 0.5 % CPU delta

    # ── Tool-Specific Profiles ───────────────────────────────────
    # These override the defaults above on a per-tool basis.
    PROFILES: Dict[str, Dict[str, Any]] = {
        "nuclei": {
            "timeout": 900,              # 15 min initial window
            "extension_seconds": 600,    # 10 min per extension
            "max_extensions": 20,
            "activity_threshold_cpu": 0.1,
            "activity_threshold_bytes": 512,
        },
        "dalfox": {
            "timeout": 600,
            "extension_seconds": 300,
            "max_extensions": 8,
        },
        "katana": {
            "timeout": 600,
            "extension_seconds": 450,
            "max_extensions": 6,
            "activity_threshold_bytes": 512,
        },
        "ffuf": {
            "timeout": 900,              # 15 min initial — content discovery is slow
            "extension_seconds": 600,
            "max_extensions": 5,
            "activity_threshold_bytes": 256,
            "activity_threshold_cpu": 0.1,
        },
        "sqlmap": {
            "timeout": 900,
            "extension_seconds": 600,
            "max_extensions": 10,
            "activity_threshold_cpu": 0.1,
        },
        "gau": {
            "timeout": 600,
            "extension_seconds": 300,
            "max_extensions": 12,
            "activity_threshold_bytes": 1024,
        },
        "subfinder": {
            "timeout": 120,
            "extension_seconds": 60,
            "max_extensions": 5,
        },
        "httpx": {
            "timeout": 300,
            "extension_seconds": 120,
            "max_extensions": 5,
        },
        "naabu": {
            "timeout": 300,
            "extension_seconds": 120,
            "max_extensions": 5,
        },
        "whatweb": {
            "timeout": 300,
            "extension_seconds": 120,
            "max_extensions": 5,
        },
        "paramspider": {
            "timeout": 300,
            "extension_seconds": 120,
            "max_extensions": 4,
        },
        "wpscan": {
            "timeout": 600,
            "extension_seconds": 300,
            "max_extensions": 5,
        },
        "trufflehog": {
            "timeout": 120,
            "extension_seconds": 60,
            "max_extensions": 3,
        },
        "gitleaks": {
            "timeout": 120,
            "extension_seconds": 60,
            "max_extensions": 3,
        },
    }

    def __init__(
        self,
        command: List[str],
        timeout: int = None,
        output_file: str = None,
        tool_name: str = None,
        max_extensions: int = None,
        extension_seconds: int = None,
        is_docker: bool = False,
    ):
        self.command = command
        self.tool_name = tool_name or (command[0].split('/')[-1] if command else "unknown")

        # Load profile if exists
        profile = self.PROFILES.get(self.tool_name.lower(), {})

        self.timeout = timeout or profile.get("timeout", self.DEFAULT_TIMEOUT)
        self.output_file = output_file
        self.max_extensions = (
            max_extensions if max_extensions is not None
            else profile.get("max_extensions", self.MAX_EXTENSIONS)
        )
        self.extension_seconds = (
            extension_seconds or profile.get("extension_seconds", self.EXTENSION_SECONDS)
        )
        self.is_docker = is_docker

        # Thresholds
        self.activity_threshold_bytes = profile.get(
            "activity_threshold_bytes", self.ACTIVITY_THRESHOLD_BYTES
        )
        self.activity_threshold_cpu = profile.get(
            "activity_threshold_cpu", self.ACTIVITY_THRESHOLD_CPU
        )

        self._process: Optional[subprocess.Popen] = None
        self._ps_proc: Optional[psutil.Process] = None
        self._extensions_used = 0
        self._start_time = 0
        self._last_io_counters = None
        self._last_cpu_time = 0
        self._last_file_size = 0
        self._stop_event = threading.Event()
        self._timed_out = False
        self._kill_reason = ""

    # ── Pre-execution Validation ─────────────────────────────────

    def validate_inputs(self) -> bool:
        """
        Pre-execution validation.
        Ensures obvious failure conditions are caught before spawning.
        """
        if not self.command:
            logger.error(f"[{self.tool_name}] Empty command provided.")
            return False

        # Tool-specific validations
        cmd_str = " ".join(self.command).lower()

        if self.tool_name.lower() == "dalfox":
            if not any(mode in cmd_str for mode in ["url", "file", "pipe", "sxss"]):
                possible_files = [
                    arg for arg in self.command
                    if arg.endswith('.txt') or arg.endswith('.json')
                ]
                if not possible_files:
                    logger.error(f"[{self.tool_name}] No target URL or input file provided.")
                    return False
                if not self.is_docker:
                    for f in possible_files:
                        if not os.path.exists(f) or os.path.getsize(f) == 0:
                            logger.error(f"[{self.tool_name}] Input file {f} is missing or empty.")
                            return False

        return True

    # ── Main Execution ───────────────────────────────────────────

    def run(self) -> str:
        """
        Main execution entry point. Returns stdout string.
        Raises ToolTimeoutError or ToolExecutionError on failure.
        """
        if not self.validate_inputs():
            raise ToolExecutionError(
                f"Pre-execution validation failed for {self.tool_name}",
                tool=self.tool_name
            )

        if self.output_file and os.path.exists(self.output_file):
            self._last_file_size = os.path.getsize(self.output_file)

        logger.info(
            f"SmartTimeoutV2: Starting {self.tool_name} "
            f"(timeout={self.timeout}s, max_ext={self.max_extensions}, "
            f"ext_window={self.extension_seconds}s)"
        )

        self._start_time = time.time()
        self._process = subprocess.Popen(
            self.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True
        )

        try:
            self._ps_proc = psutil.Process(self._process.pid)
            self._update_metrics()
        except psutil.NoSuchProcess:
            pass  # Process might have finished instantly

        # Start background monitor
        monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        monitor_thread.start()

        try:
            stdout, stderr = self._process.communicate()
            self._stop_event.set()
        except Exception:
            self._stop_event.set()
            self._terminate_gracefully()
            raise

        elapsed = time.time() - self._start_time

        if self._timed_out:
            raise ToolTimeoutError(
                f"Tool '{self.tool_name}' killed after {elapsed:.0f}s — "
                f"Reason: {self._kill_reason}",
                tool=self.tool_name
            )

        if self._process.returncode != 0:
            # Check if it produced output despite error code (partial success)
            if self._has_new_output():
                logger.warning(
                    f"{self.tool_name} exited with code {self._process.returncode} "
                    f"but produced output — treating as partial success."
                )
                return stdout

            raise ToolExecutionError(
                f"'{self.tool_name}' failed with exit code {self._process.returncode}. "
                f"stderr: {stderr[:500]}",
                tool=self.tool_name
            )

        logger.success(
            f"SmartTimeoutV2: {self.tool_name} completed in {elapsed:.1f}s "
            f"(extensions used: {self._extensions_used})"
        )
        return stdout

    # ── Background Monitor ───────────────────────────────────────

    def _monitor_loop(self):
        """
        Background thread that enforces deadlines and detects activity.
        """
        next_check = self._start_time + self.timeout

        while not self._stop_event.is_set():
            now = time.time()
            if now < next_check:
                time.sleep(1)
                continue

            # Process already exited naturally
            if self._process.poll() is not None:
                break

            # ── Hard ceiling: max extensions exhausted ───────────
            if self._extensions_used >= self.max_extensions:
                self._timed_out = True
                self._kill_reason = (
                    f"Max extensions exhausted ({self._extensions_used}/{self.max_extensions}) "
                    f"after {now - self._start_time:.0f}s total runtime"
                )
                logger.warning(
                    f"SmartTimeoutV2: Terminating {self.tool_name} — {self._kill_reason}"
                )
                self._terminate_gracefully()
                break

            # ── Activity check ───────────────────────────────────
            is_active = self._check_activity()

            if is_active:
                self._extensions_used += 1
                logger.info(
                    f"SmartTimeoutV2: Extending {self.tool_name} by {self.extension_seconds}s "
                    f"({self._extensions_used}/{self.max_extensions}) — Activity detected."
                )
                next_check = now + self.extension_seconds
            else:
                self._timed_out = True
                self._kill_reason = (
                    f"No activity detected for {self.extension_seconds}s "
                    f"(total runtime: {now - self._start_time:.0f}s)"
                )
                logger.warning(
                    f"SmartTimeoutV2: Terminating {self.tool_name} — {self._kill_reason}"
                )
                self._terminate_gracefully()
                break

    # ── Metrics Collection ───────────────────────────────────────

    def _get_total_cpu_time(self, proc: psutil.Process) -> float:
        """Sums user and system CPU times for a process."""
        try:
            times = proc.cpu_times()
            return times.user + times.system
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0.0

    def _update_metrics(self):
        """Captures current process metrics as baseline."""
        try:
            self._last_cpu_time = self._get_total_cpu_time(self._ps_proc)
            try:
                self._last_io_counters = self._ps_proc.io_counters()
            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                self._last_io_counters = None
            if self.output_file and os.path.exists(self.output_file):
                self._last_file_size = os.path.getsize(self.output_file)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    def _check_activity(self) -> bool:
        """
        Determines if the process is "active" based on multiple signals.

        In Docker mode, CPU/IO metrics reflect the thin `docker exec` wrapper
        on the host, not the actual workload inside the container. Output-file
        growth is therefore treated as the primary signal.
        """
        has_output = self._has_new_output()

        if has_output:
            self._update_metrics()
            return True

        # Check CPU / IO delta
        try:
            curr_cpu = self._get_total_cpu_time(self._ps_proc)
            cpu_delta = curr_cpu - self._last_cpu_time

            io_delta_total = 0
            if self._last_io_counters is not None:
                try:
                    curr_io = self._ps_proc.io_counters()
                    io_delta_read = curr_io.read_bytes - self._last_io_counters.read_bytes
                    io_delta_write = curr_io.write_bytes - self._last_io_counters.write_bytes
                    io_delta_total = io_delta_read + io_delta_write
                    self._last_io_counters = curr_io
                except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                    pass

            self._last_cpu_time = curr_cpu

            logger.debug(
                f"[{self.tool_name}] Activity: CPU Δ={cpu_delta:.3f}s, "
                f"IO Δ={io_delta_total} bytes"
            )

            if not self.is_docker:
                # Direct mode — trust all metrics
                if cpu_delta > self.activity_threshold_cpu:
                    return True
                if io_delta_total > self.activity_threshold_bytes:
                    return True
                # Check children
                for child in self._ps_proc.children(recursive=True):
                    try:
                        c_cpu = self._get_total_cpu_time(child)
                        if c_cpu > 0:
                            return True
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            else:
                # Docker mode — any measurable delta counts
                if cpu_delta > 0 or io_delta_total > 0:
                    return True
                # Docker fallback: check if the process tree has children
                # (the docker exec shim always has the container process)
                try:
                    children = self._ps_proc.children(recursive=True)
                    for child in children:
                        try:
                            if child.status() != psutil.STATUS_ZOMBIE:
                                child_cpu = self._get_total_cpu_time(child)
                                if child_cpu > 0:
                                    return True
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
            pass

        return False

    def _has_new_output(self) -> bool:
        """Checks if the output file grew since last check."""
        if not self.output_file or not os.path.exists(self.output_file):
            return False

        current_size = os.path.getsize(self.output_file)
        if current_size > self._last_file_size:
            self._last_file_size = current_size
            return True
        return False

    # ── Graceful Termination ────────────────────────────────────

    def _terminate_gracefully(self):
        """
        Gracefully shuts down the process and its children.
        SIGTERM → wait 5s → SIGKILL for survivors.
        """
        if not self._process or self._process.poll() is not None:
            return

        logger.info(
            f"SmartTimeoutV2: Graceful shutdown for {self.tool_name} "
            f"(pid {self._process.pid})"
        )

        try:
            parent = psutil.Process(self._process.pid)
            children = parent.children(recursive=True)

            parent.terminate()
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    continue

            gone, alive = psutil.wait_procs([parent] + children, timeout=5)

            for p in alive:
                logger.warning(
                    f"SmartTimeoutV2: Force-killing pid {p.pid} "
                    f"({self.tool_name})"
                )
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    continue

        except Exception as e:
            logger.error(f"Error during termination of {self.tool_name}: {e}")
            try:
                self._process.kill()
            except Exception:
                pass
