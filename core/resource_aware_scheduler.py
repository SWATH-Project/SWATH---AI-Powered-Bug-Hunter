#!/usr/bin/env python3
"""
Resource-Aware Adaptive Scheduler for SWATH

Intelligently schedules tools based on:
- Phase type (light vs heavy)
- Available system resources (RAM, CPU)
- Current system load (including user activity)
- Tool resource profiles

Goal: Maximize throughput while preventing OOM and keeping system responsive.
"""

import os
import sys
import time
import psutil
import threading
import logging
import yaml
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, NamedTuple
from pathlib import Path
from collections import deque
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Data Structures
# -----------------------------------------------------------------------------

@dataclass
class SystemCapacity:
    """Current system capacity for scheduling decisions"""
    total_ram_gb: float
    available_ram_gb: float
    total_cpu_cores: int
    cpu_percent: float
    load_avg_1min: float
    swap_used_percent: float
    user_active: bool  # Is user currently using the system?
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def effective_ram_gb(self) -> float:
        """RAM available for tools, with safety margin and swap adjustment"""
        # Start with available RAM
        effective = self.available_ram_gb

        # Apply safety margin (keep 30% for OS on small systems, 20% on large)
        if self.total_ram_gb < 8:
            effective *= 0.7  # 30% margin on small systems
        else:
            effective *= 0.8  # 20% margin on large systems

        # If swap is heavily used, be more conservative
        if self.swap_used_percent > 50:
            effective *= 0.7  # Penalize 30% if swapping

        # If user is active, leave more RAM for them
        if self.user_active:
            effective *= 0.7  # Use only 70% of calculated if user active

        return max(0.5, effective)  # Minimum 0.5GB even on tiny systems

    @property
    def pressure_level(self) -> str:
        """Overall system pressure: low, medium, high, critical"""
        if self.cpu_percent > 85 or self.load_avg_1min > (self.total_cpu_cores * 1.5):
            return "critical"
        elif self.cpu_percent > 70 or self.load_avg_1min > (self.total_cpu_cores * 1.0):
            return "high"
        elif self.cpu_percent > 50 or self.load_avg_1min > (self.total_cpu_cores * 0.7):
            return "medium"
        else:
            return "low"


@dataclass
class ToolEstimate:
    """Resource estimate for a tool with specific parameters"""
    tool_name: str
    memory_gb: float
    cpu_cores: float
    estimated_time_min: int
    parameters: Dict[str, any] = field(default_factory=dict)

    def can_fit_in(self, available_ram_gb: float, available_cpu_cores: float) -> bool:
        """Check if this tool execution fits in remaining resources"""
        return (self.memory_gb < available_ram_gb * 0.9 and
                self.cpu_cores < available_cpu_cores * 0.9)


@dataclass
class SchedulingDecision:
    """Result of scheduler decision"""
    tool_name: str
    action: str  # "run", "wait", "skip", "scale"
    reason: str
    suggested_parameters: Optional[Dict[str, any]] = None
    estimated_memory_gb: Optional[float] = None
    estimated_time_min: Optional[int] = None
    priority: int = 1  # Lower = higher priority


class ToolProfiles:
    """Loads and provides tool resource profiles"""

    def __init__(self, profiles_path: str = "data/tool_profiles.yaml"):
        self.profiles_path = Path(profiles_path)
        self.tool_profiles: Dict[str, dict] = {}
        self.phase_weights: Dict[str, str] = {}
        self.phase_limits: Dict[str, dict] = {}
        self.resource_thresholds: dict = {}
        self._load()

    def _load(self):
        """Load profiles from YAML, with graceful fallback to defaults"""
        try:
            if self.profiles_path.exists():
                with open(self.profiles_path) as f:
                    data = yaml.safe_load(f)

                self.tool_profiles = data.get('tool_profiles', {})
                self.phase_weights = data.get('phase_weights', {})
                self.phase_limits = self.phase_weights
                self.resource_thresholds = data.get('resource_thresholds', {})
                logger.info(f"Loaded {len(self.tool_profiles)} tool profiles from {self.profiles_path}")
            else:
                logger.warning(f"Tool profiles not found at {self.profiles_path}, using defaults")
                self.tool_profiles = {}
                self.phase_weights = {}
                self.phase_limits = {}
                self.resource_thresholds = {}
        except Exception as e:
            logger.error(f"Failed to load tool profiles: {e}. Using defaults.")
            self.tool_profiles = {}
            self.phase_weights = {}
            self.phase_limits = {}
            self.resource_thresholds = {}

    def get_tool_profile(self, tool_name: str) -> dict:
        """Get resource profile for a tool"""
        if tool_name not in self.tool_profiles:
            logger.warning(f"No profile for tool {tool_name}, using defaults")
            return {
                'phase': 'unknown',
                'phase_weight': 'medium',
                'base_memory_mb': 500,
                'base_cpu_cores': 1.0,
                'estimated_time_min': 30,
                'scalable_params': {}
            }
        return self.tool_profiles[tool_name]

    def get_phase_weight(self, phase_name: str) -> str:
        """Get weight classification for a phase"""
        return self.phase_weights.get(phase_name, {}).get('weight', 'medium')

    def get_phase_concurrency_limit(self, phase_name: str, hardware_class: str) -> int:
        """Get max concurrent tools for this phase and hardware class"""
        phase_config = self.phase_weights.get(phase_name, {})
        key = f"max_concurrent_{hardware_class}_hw"
        return phase_config.get(key, 2)  # Default 2

    def estimate_tool_resources(self, tool_name: str, param_overrides: Dict[str, any] = None) -> ToolEstimate:
        """Estimate memory/CPU for tool with given parameters"""
        profile = self.get_tool_profile(tool_name)

        # Start with base values
        memory_mb = profile['base_memory_mb']
        cpu_cores = profile['base_cpu_cores']
        time_min = profile['estimated_time_min']

        # Apply parameter scaling
        scalable_params = profile.get('scalable_params', {})
        if param_overrides:
            for param, value in param_overrides.items():
                if param in scalable_params:
                    spec = scalable_params[param]
                    # Calculate impact
                    if 'memory_per_unit_mb' in spec:
                        memory_mb += spec['memory_per_unit_mb'] * (value - spec.get('value', value))
                    if 'cpu_per_unit' in spec:
                        cpu_cores += spec['cpu_per_unit'] * (value - spec.get('value', value))
                    if 'memory_impact_mb' in spec:
                        memory_mb += spec['memory_impact_mb']
                    if 'cpu_impact' in spec:
                        cpu_cores += spec['cpu_impact']

        # Account for boolean flags with memory impact
        for param, spec in scalable_params.items():
            if (param_overrides is None or param not in param_overrides) and 'memory_impact_mb' in spec:
                # Apply impact for enabled features that are profile defaults
                if spec.get('value') is True or (isinstance(spec.get('value'), str) and spec['value'].lower() == 'true'):
                    memory_mb += spec['memory_impact_mb']
                    cpu_cores += spec.get('cpu_impact', 0)

        return ToolEstimate(
            tool_name=tool_name,
            memory_gb=memory_mb / 1024,
            cpu_cores=max(0.1, cpu_cores),
            estimated_time_min=time_min,
            parameters=param_overrides or {}
        )


# -----------------------------------------------------------------------------
# Resource Monitor
# -----------------------------------------------------------------------------

class ResourceMonitor:
    """Continuously monitors system resources"""

    def __init__(self, update_interval: float = 2.0):
        self.update_interval = update_interval
        self._running = False
        self._thread = None
        self._current_capacity: Optional[SystemCapacity] = None
        self._lock = threading.Lock()

    def start(self):
        """Start background monitoring thread"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def _monitor_loop(self):
        """Background monitoring loop"""
        while self._running:
            try:
                capacity = self._snapshot()
                with self._lock:
                    self._current_capacity = capacity
            except Exception as e:
                logger.error(f"Monitoring error: {e}")

            time.sleep(self.update_interval)

    def _snapshot(self) -> SystemCapacity:
        """Take a snapshot of current system state"""
        mem = psutil.virtual_memory()
        cpu_percent = psutil.cpu_percent(interval=0.1)
        cpu_cores = psutil.cpu_count(logical=True) or 1

        if hasattr(os, 'getloadavg'):
            load_avg = os.getloadavg()[0]
        else:
            load_avg = cpu_percent / 100.0 * cpu_cores

        swap = psutil.swap_memory()

        # Detect user activity (simple heuristic: CPU usage from user processes + interactive processes)
        user_active = self._check_user_activity()

        return SystemCapacity(
            total_ram_gb=mem.total / (1024**3),
            available_ram_gb=mem.available / (1024**3),
            total_cpu_cores=cpu_cores,
            cpu_percent=cpu_percent,
            load_avg_1min=load_avg,
            swap_used_percent=swap.percent,
            user_active=user_active
        )

    def _check_user_activity(self) -> bool:
        """Check if user is actively using the system"""
        try:
            if sys.platform.startswith('linux'):
                cores = psutil.cpu_count() or 1
                if hasattr(os, 'getloadavg'):
                    load = os.getloadavg()[0]
                    if load > cores * 0.8:
                        return True
                else:
                    if psutil.cpu_percent(interval=0.5) > 70:
                        return True

                # Check for GUI processes (Firefox, VS Code, etc.)
                for proc in psutil.process_iter(['name']):
                    name = proc.info['name'] or ''
                    if any(app in name.lower() for app in ['firefox', 'chrome', 'code', 'terminal']):
                        # Check if process is using CPU
                        try:
                            if proc.cpu_percent(interval=0.1) > 5:
                                return True
                        except:
                            pass

            elif sys.platform == 'darwin':
                cores = psutil.cpu_count() or 1
                if hasattr(os, 'getloadavg'):
                    load = os.getloadavg()[0]
                    if load > cores * 0.8:
                        return True
                else:
                    if psutil.cpu_percent(interval=0.5) > 70:
                        return True

            elif sys.platform == 'win32':
                cpu = psutil.cpu_percent(interval=0.5)
                if cpu > 70:
                    return True

            return False
        except Exception as e:
            logger.debug(f"User activity check failed: {e}")
            return False

    def get_capacity(self) -> SystemCapacity:
        """Get current system capacity (thread-safe)"""
        with self._lock:
            if self._current_capacity:
                return self._current_capacity
            # Fallback to immediate snapshot
            return self._snapshot()

    def stop(self):
        """Stop monitoring"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)


# -----------------------------------------------------------------------------
# Adaptive Scheduler
# -----------------------------------------------------------------------------

class AdaptiveScheduler:
    """
    Core scheduling engine that adapts to system resources.

    Strategy differs by phase weight:
    - Light: High concurrency, minimal throttling
    - Medium: Adaptive concurrency, gentle throttling
    - Heavy: Low concurrency, aggressive parameter scaling
    """

    def __init__(self, tool_profiles: ToolProfiles, resource_monitor: ResourceMonitor):
        self.profiles = tool_profiles
        self.monitor = resource_monitor
        self._running_tools: Dict[str, ToolEstimate] = {}
        self._lock = threading.Lock()
        self.phase_type_cache: Dict[str, str] = {}  # phase_name -> weight

    def get_phase_strategy(self, phase_name: str) -> str:
        """Get scheduling strategy for this phase"""
        phase_config = self.profiles.phase_weights.get(phase_name, {})
        return phase_config.get('scheduler_strategy', 'adaptive_with_throttle')

    def get_concurrency_limit(self, phase_name: str) -> int:
        """Get max concurrent tools for this phase based on current hardware"""
        capacity = self.monitor.get_capacity()
        phase_config = self.profiles.phase_weights.get(phase_name, {})

        # Determine hardware class based on total RAM
        if capacity.total_ram_gb >= 16:
            hardware_class = "light"  # High-end hardware uses "light" limits (high concurrency)
        elif capacity.total_ram_gb >= 8:
            hardware_class = "medium"
        else:
            hardware_class = "low"

        # Get baseline limit from config
        baseline = phase_config.get(f"max_concurrent_{hardware_class}_hw", 2)

        # Adjust based on current pressure
        pressure = capacity.pressure_level
        if pressure == "critical":
            baseline = max(1, baseline - 2)
        elif pressure == "high":
            baseline = max(1, baseline - 1)
        elif pressure == "low" and not capacity.user_active:
            # If system idle and user not active, we can push harder
            if hardware_class == "light":
                baseline = baseline + 2  # Aggressive on high-end idle systems

        return baseline

    def can_schedule(self, tool_name: str, phase_name: str, current_concurrent: int,
                     param_overrides: Dict[str, any] = None) -> SchedulingDecision:
        """
        Decide if a tool can be started now.

        Returns: SchedulingDecision with action (run/wait/skip) and reasoning
        """
        capacity = self.monitor.get_capacity()
        phase_weight = self.profiles.get_phase_weight(phase_name)
        strategy = self.get_phase_strategy(phase_name)

        # 1. Check concurrent limit for this phase
        concurrency_limit = self.get_concurrency_limit(phase_name)
        if current_concurrent >= concurrency_limit:
            return SchedulingDecision(
                tool_name=tool_name,
                action="wait",
                reason=f"Concurrency limit ({concurrency_limit}) reached for phase {phase_name}"
            )

        # 2. Estimate tool resources with current parameters
        estimate = self.profiles.estimate_tool_resources(tool_name, param_overrides)

        # 3. Check if tool fits in available RAM
        effective_ram = capacity.effective_ram_gb
        current_used_ram = sum(t.memory_gb for t in self._running_tools.values())
        available_ram = effective_ram - current_used_ram

        if not estimate.can_fit_in(available_ram, capacity.total_cpu_cores * 0.9):
            # Adaptive: If tool doesn't fit, can we scale it down?
            if strategy in ["adaptive_with_params_scaling", "adaptive_sequential_with_params_scaling"]:
                scaled_params = self._scale_params_for_fit(tool_name, available_ram)
                if scaled_params:
                    # Re-estimate with scaled params
                    estimate = self.profiles.estimate_tool_resources(tool_name, scaled_params)
                    if estimate.can_fit_in(available_ram, capacity.total_cpu_cores * 0.9):
                        return SchedulingDecision(
                            tool_name=tool_name,
                            action="run",
                            reason="Running with scaled parameters to fit memory budget",
                            suggested_parameters=scaled_params,
                            estimated_memory_gb=estimate.memory_gb,
                            estimated_time_min=estimate.estimated_time_min,
                            priority=2
                        )

            # For heavy phases, just wait if we can't fit
            if phase_weight in ['heavy', 'very_heavy']:
                return SchedulingDecision(
                    tool_name=tool_name,
                    action="wait",
                    reason=f"Insufficient RAM: need {estimate.memory_gb:.1f}GB, have {available_ram:.1f}GB available",
                    estimated_memory_gb=estimate.memory_gb
                )
            else:
                # For light phases, be more lenient - still warn but allow if memory seems available
                if available_ram < estimate.memory_gb * 1.2:
                    return SchedulingDecision(
                        tool_name=tool_name,
                        action="wait",
                        reason=f"Low memory warning: need {estimate.memory_gb:.1f}GB, have {available_ram:.1f}GB"
                    )

        # 4. Check CPU pressure
        if capacity.cpu_percent > 85 and phase_weight in ['heavy', 'very_heavy']:
            return SchedulingDecision(
                tool_name=tool_name,
                action="wait",
                reason=f"CPU overload: {capacity.cpu_percent}% used"
            )

        # 5. Check if user is very active (be nice)
        if capacity.user_active and phase_weight in ['heavy', 'very_heavy']:
            # For heavy tools, if user is active, wait or run with lower priority
            if current_concurrent > 0:
                return SchedulingDecision(
                    tool_name=tool_name,
                    action="wait",
                    reason="User is active, deferring heavy tool"
                )
            # If no other tools running, we can run but with nice priority
            return SchedulingDecision(
                tool_name=tool_name,
                action="run",
                reason="Running with lower priority due to user activity",
                suggested_parameters={'nice': 10} if sys.platform != 'win32' else None,
                priority=3
            )

        # All checks passed - tool can run
        return SchedulingDecision(
            tool_name=tool_name,
            action="run",
            reason="Resources available",
            suggested_parameters=param_overrides,
            estimated_memory_gb=estimate.memory_gb,
            estimated_time_min=estimate.estimated_time_min,
            priority=1
        )

    def _scale_params_for_fit(self, tool_name: str, available_ram_gb: float) -> Optional[Dict[str, any]]:
        """
        Try to scale tool parameters down so it fits in available RAM.
        Returns new parameters or None if impossible.
        """
        profile = self.profiles.get_tool_profile(tool_name)
        scalable = profile.get('scalable_params', {})

        if not scalable:
            return None  # Can't scale this tool

        # Start with current tool running (baseline memory usage)
        current_memory_mb = profile['base_memory_mb']

        # Calculate how much we need to reduce
        needed_ram_mb = available_ram_gb * 1024 * 0.8  # Use 80% of available
        if current_memory_mb <= needed_ram_mb:
            return {}  # Already fits, no scaling needed

        excess_mb = current_memory_mb - needed_ram_mb
        new_params = {}

        # Try to reduce scalable parameters proportionally
        for param, spec in scalable.items():
            if 'memory_per_unit_mb' in spec:
                current_value = spec.get('value', 1)
                per_unit = spec['memory_per_unit_mb']
                min_val = spec.get('min', 1)
                max_val = spec.get('max', current_value)

                # Calculate reduction needed
                reduction_needed = excess_mb / per_unit if per_unit > 0 else 0
                new_value = max(min_val, current_value - reduction_needed)

                if new_value < current_value:
                    new_params[param] = int(new_value)
                    # Recalculate memory savings
                    saved = (current_value - new_value) * per_unit
                    excess_mb -= saved
                    if excess_mb <= 0:
                        break

        # Check if scaling gets us under limit
        if excess_mb > 0:
            logger.warning(f"Could not scale {tool_name} enough to fit (still {excess_mb:.0f}MB over)")
            return None

        return new_params

    def register_tool_start(self, tool_name: str, estimate: ToolEstimate):
        """Record that a tool has started"""
        with self._lock:
            self._running_tools[tool_name] = estimate
        logger.info(f"Started {tool_name} (est: {estimate.memory_gb:.1f}GB RAM, {estimate.estimated_time_min}min)")

    def register_tool_end(self, tool_name: str):
        """Record that a tool has finished"""
        with self._lock:
            if tool_name in self._running_tools:
                del self._running_tools[tool_name]

    def get_running_tools(self) -> List[str]:
        """Get list of currently running tools"""
        with self._lock:
            return list(self._running_tools.keys())

    def get_current_usage(self) -> Tuple[float, float]:
        """Get current total RAM and CPU usage of running tools"""
        with self._lock:
            total_ram = sum(t.memory_gb for t in self._running_tools.values())
            total_cpu = sum(t.cpu_cores for t in self._running_tools.values())
        return total_ram, total_cpu

    def should_throttle(self) -> bool:
        """Check if system is under pressure and we should slow down"""
        capacity = self.monitor.get_capacity()
        return capacity.pressure_level in ['high', 'critical']

    def get_system_status(self) -> dict:
        """Get current system status for logging/display"""
        capacity = self.monitor.get_capacity()
        ram_used, cpu_used = self.get_current_usage()

        return {
            'timestamp': capacity.timestamp.isoformat(),
            'ram': {
                'total_gb': capacity.total_ram_gb,
                'available_gb': capacity.available_ram_gb,
                'effective_gb': capacity.effective_ram_gb,
                'used_by_tools_gb': ram_used,
                'percent_used': (ram_used / capacity.total_ram_gb) * 100
            },
            'cpu': {
                'cores': capacity.total_cpu_cores,
                'percent': capacity.cpu_percent,
                'used_by_tools': cpu_used,
                'load_avg': capacity.load_avg_1min
            },
            'swap_used_percent': capacity.swap_used_percent,
            'user_active': capacity.user_active,
            'pressure_level': capacity.pressure_level,
            'running_tools': len(self._running_tools),
            'tool_list': list(self._running_tools.keys())
        }


# -----------------------------------------------------------------------------
# Convenience Functions
# -----------------------------------------------------------------------------

def create_scheduler() -> AdaptiveScheduler:
    """Factory function to create scheduler with default components"""
    profiles = ToolProfiles()
    monitor = ResourceMonitor()
    monitor.start()
    return AdaptiveScheduler(profiles, monitor)


# -----------------------------------------------------------------------------
# Testing
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("Testing Adaptive Scheduler...")

    monitor = ResourceMonitor()
    monitor.start()
    profiles = ToolProfiles()
    scheduler = AdaptiveScheduler(profiles, monitor)

    # Wait for initial snapshot
    time.sleep(2)

    print("\nSystem Status:")
    status = monitor.get_capacity()
    print(f"  RAM: {status.total_ram_gb:.1f} GB total, {status.available_ram_gb:.1f} GB available")
    print(f"  CPU: {status.total_cpu_cores} cores, {status.cpu_percent}% utilization")
    print(f"  Load: {status.load_avg_1min:.2f}")
    print(f"  User active: {status.user_active}")
    print(f"  Pressure: {status.pressure_level}")

    print("\nPhase Concurrency Limits:")
    for phase in ['phase_1_passive_recon', 'phase_6_content_discovery', 'phase_7_vuln_scan']:
        limit = scheduler.get_concurrency_limit(phase)
        print(f"  {phase}: {limit} concurrent tools")

    print("\nTool Estimates:")
    for tool in ['subfinder', 'nuclei', 'sqlmap', 'ffuf']:
        estimate = profiles.estimate_tool_resources(tool)
        print(f"  {tool}: {estimate.memory_gb:.1f}GB RAM, {estimate.cpu_cores:.1f} CPU cores, ~{estimate.estimated_time_min}min")

    print("\nScheduling Decision Examples:")
    decision = scheduler.can_schedule('subfinder', 'phase_1_passive_recon', 0)
    print(f"  subfinder in Phase 1: {decision.action} - {decision.reason}")

    decision = scheduler.can_schedule('sqlmap', 'phase_7_vuln_scan', 0)
    print(f"  sqlmap in Phase 7: {decision.action} - {decision.reason}")

    # Clean shutdown
    monitor.stop()
    print("\n✓ Scheduler test complete")
