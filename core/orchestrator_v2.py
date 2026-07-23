#!/usr/bin/env python3
"""
SWATH Orchestrator V2 - Resource-Aware Adaptive Scheduling

Professional-grade orchestrator. Runs exactly what the methodology YAML defines.
No profile filtering — the methodology IS the single source of truth.

Key features:
- Adaptive concurrency based on system resources
- Phase-specific scheduling strategies (light vs heavy)
- Parameter scaling (reduce threads if memory constrained)
- Checkpoint/resume capability
- Optional Phase 7 (user selects targets after recon)
- Resource monitoring throughout
"""

import os
import gc
import yaml
import json
import time
import sys
import signal
import inspect
import platform
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
import concurrent.futures
import threading

from loguru import logger

from core.tag_manager import TagManager
from core.budget_tracker import BudgetTracker
from core.scan_history import ScanHistory
from core.hf_logger import HFLogger
from core.exceptions import (
    SWATHError, OutOfScopeError, BudgetExceededError,
    BinaryNotFoundError, ToolTimeoutError, ToolExecutionError
)

from core.resource_aware_scheduler import (
    AdaptiveScheduler,
    ToolProfiles,
    ResourceMonitor,
    SystemCapacity
)

# ═══════════════════════════════════════════════════════════════════
# TOOL IMPORTS — Professional methodology only (16 essential tools)
# ═══════════════════════════════════════════════════════════════════

# Phase 1 — Passive Recon (3 tools)
from modules.passive.subfinder import SubfinderModule as Subfinder
from modules.passive.crtsh import CrtshModule as Crtsh

# Phase 2 — Secrets & OSINT (2 tools)
from modules.secrets.gitleaks import GitleaksModule as Gitleaks
from modules.secrets.trufflehog import TrufflehogModule as Trufflehog

# Phase 3 — Live Asset Discovery (2 tools)
from modules.discovery.httpx import HttpxModule as Httpx
from modules.discovery.naabu import NaabuModule as Naabu

# Phase 4 — Surface Intelligence (2 tools)
from modules.surface_intel.whatweb import WhatWebModule as WhatWeb
from modules.surface_intel.wappalyzer import WappalyzerModule as Wappalyzer

# Phase 5 — Enumeration (5 tools, 2 conditional)
from modules.enumeration.katana import KatanaModule as Katana
from modules.enumeration.gau import GauModule as Gau
from modules.enumeration.paramspider import ParamspiderModule as Paramspider
from modules.enumeration.arjun import ArjunModule as Arjun
from modules.enumeration.graphql_voyager import GraphqlVoyagerModule as GraphqlVoyager

# Phase 6 — Content Discovery (2 tools, 1 conditional)
from modules.content_discovery.ffuf import FfufModule as Ffuf
from modules.content_discovery.wpscan import WpscanModule as Wpscan

# Phase 7 — Vulnerability Scanning (5 tools, 3 conditional)
from modules.vuln_scan.nuclei import NucleiModule as Nuclei
from modules.vuln_scan.subjack import SubjackModule as Subjack
from modules.vuln_scan.dalfox import DalfoxModule as Dalfox
from modules.vuln_scan.sqlmap import SQLMapModule as SQLMap

# ═══════════════════════════════════════════════════════════════════
# TOOL REGISTRY — maps YAML tool names to Python classes
# ═══════════════════════════════════════════════════════════════════
from core.plugin_loader import PluginLoader
TOOL_REGISTRY = PluginLoader.discover()
# Explicitly map nuclei_auth to Nuclei as it uses the same module
if 'nuclei' in TOOL_REGISTRY:
    TOOL_REGISTRY['nuclei_auth'] = TOOL_REGISTRY['nuclei']
# Add original ones if missing just in case
if 'subfinder' not in TOOL_REGISTRY: TOOL_REGISTRY['subfinder'] = Subfinder
if 'httpx' not in TOOL_REGISTRY: TOOL_REGISTRY['httpx'] = Httpx
if 'nuclei' not in TOOL_REGISTRY: TOOL_REGISTRY['nuclei'] = Nuclei


class OrchestratorV2:
    """
    Professional-grade orchestrator with adaptive resource scheduling.

    Key principles:
    1. The methodology YAML is the single source of truth
    2. No profile filtering — execute exactly what the YAML defines
    3. Adaptive scheduling based on real-time system resources
    4. Checkpoint/resume for long-running scans
    5. Optional Phase 7 with human decision point
    """

    def __init__(self, domain: str, methodology_path: str,
                 checkpoint_file: Optional[str] = None, adaptive: bool = True,
                 only_phase: Optional[str] = None, override_input_file: Optional[str] = None,
                 scan_id: Optional[int] = None):
        self.domain = domain
        self.adaptive = adaptive
        self.only_phase = only_phase
        self.override_input_file = override_input_file
        self.methodology_path = Path(methodology_path)
        self.checkpoint_file = checkpoint_file or f"output/{domain}/checkpoint.json"
        self.scan_id = scan_id  # Dashboard tracking ID

        # Load methodology
        with open(self.methodology_path) as f:
            self.methodology = yaml.safe_load(f)

        budget_config = self.methodology.get('budget', {})
        max_requests = budget_config.get('max_requests_total') if budget_config.get('enabled', True) else None

        # Initialize core components
        self.tag_manager = TagManager()
        self.budget_tracker = BudgetTracker(max_requests=max_requests)
        self.scan_history = ScanHistory()
        self.logger = HFLogger(f"output/{domain}")

        # Initialize scheduler
        self.resource_monitor = ResourceMonitor(update_interval=2.0)
        self.tool_profiles = ToolProfiles()
        self.scheduler = AdaptiveScheduler(self.tool_profiles, self.resource_monitor)

        # Runtime state
        self.completed_tools: List[Dict[str, Any]] = []
        self.current_phase = None
        self.current_tool = None
        self.scan_start_time = None
        self._shutdown = False
        self.lock = threading.Lock()
        self._total_tools_count = 0

        # Signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._handle_signal)
        if platform.system() != 'Windows':
            signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.warning(f"Received signal {signum}, initiating graceful shutdown...")
        self._shutdown = True

    def load_checkpoint(self) -> bool:
        """Load existing checkpoint if exists"""
        checkpoint_path = Path(self.checkpoint_file)
        if checkpoint_path.exists():
            try:
                with open(checkpoint_path) as f:
                    data = json.load(f)

                self.completed_tools = data.get('completed_tools', [])
                self.tag_manager.tags = data.get('tags', {})
                
                # Restore budget state
                budget_data = data.get('budget')
                if budget_data:
                    self.budget_tracker.requests_used = budget_data.get('requests_used', 0)
                    self.budget_tracker.start_time = budget_data.get('start_time', time.time())
                    logger.info(f"Restored budget: {self.budget_tracker.requests_used} requests already counted")

                logger.info(f"Resumed from checkpoint: {len(self.completed_tools)} tools already completed")
                return True
            except Exception as e:
                logger.error(f"Failed to load checkpoint: {e}")
        return False

    def save_checkpoint(self):
        """Save current state to checkpoint file"""
        checkpoint_dir = Path(self.checkpoint_file).parent
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        with self.lock:
            data = {
                'domain': self.domain,
                'phase': self.current_phase,
                'completed_tools': list(self.completed_tools),
                'tags': self.tag_manager.get_all(),
                'budget': {
                    'requests_used': self.budget_tracker.requests_used,
                    'start_time': self.budget_tracker.start_time
                },
                'timestamp': datetime.utcnow().isoformat() + 'Z'
            }

        try:
            with open(self.checkpoint_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Checkpoint saved: {len(self.completed_tools)} tools completed")
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")

    def should_skip_tool(self, tool_config: dict, tool_name: str) -> Tuple[bool, str]:
        """Check if tool should be skipped (already done or conditional)"""
        # Check if already completed in this scan
        if any(ct['tool'] == tool_name for ct in self.completed_tools):
            return True, "Already completed in this scan"

        # Check if_tag condition
        if_tag = tool_config.get('if_tag')
        if if_tag:
            if not self.tag_manager.has(if_tag):
                return True, f"Tag '{if_tag}' not set"

        # Check enabled flag
        if tool_config.get('enabled', True) is False:
            return True, "Tool disabled in methodology"

        return False, ""

    def run_phase(self, phase_name: str, phase_config: dict):
        """Execute a single phase with adaptive scheduling"""
        phase_label = phase_config.get('label', phase_name)
        logger.info(f"Starting phase: {phase_label}")
        self.current_phase = phase_name
        self.current_tool = None

        # Broadcast phase start to dashboard
        self._broadcast_live_state(phase=phase_name, tool=None, status='phase_running')

        self.logger.phase_start(phase_name, phase_label)

        # Support both 'tools' (phases 1-4) and 'conditional_tools' (phases 5-7)
        tools = phase_config.get('tools') or phase_config.get('conditional_tools') or []
        input_files = phase_config.get('input_files', {})

        # Build list of tools to run
        tools_to_run = []
        for tool_entry in tools:
            # Handle both dict and simple string formats
            if isinstance(tool_entry, dict):
                tool_name = tool_entry.get('tool') or tool_entry.get('name')
                if not tool_name:
                    continue
                tool_config = tool_entry
            else:
                tool_name = tool_entry
                tool_config = {}

            skip, reason = self.should_skip_tool(tool_config, tool_name)
            if skip:
                logger.info(f"Skipping {tool_name}: {reason}")
                self.logger.tool_skipped(tool_name, reason)
                continue

            if tool_name not in TOOL_REGISTRY:
                logger.warning(f"Tool {tool_name} not in registry, skipping")
                self.logger.tool_skipped(tool_name, "Not in tool registry")
                continue

            tools_to_run.append({
                'name': tool_name,
                'config': tool_config,
                '_class': TOOL_REGISTRY[tool_name]
            })

        logger.info(f"Phase {phase_name}: {len(tools_to_run)} tools to execute")

        # Concurrent scheduling loop — tools run in parallel threads managed by scheduler
        completed = 0
        failed = 0
        total = len(tools_to_run)

        def _tool_worker(tool_info):
            if self._shutdown:
                return 'skipped'

            tool_name = tool_info['name']
            tool_config = tool_info.get('config', {})

            # Scheduler blocks or limits concurrency natively inside this block
            try:
                decision = self.scheduler.can_schedule(
                    tool_name,
                    phase_name,
                    0,
                    tool_config
                )

                if decision.action == 'wait':
                    logger.warning(
                        f"Insufficient resources for {tool_name}: {decision.reason} — "
                        f"running with scaled parameters"
                    )
                    if decision.suggested_parameters:
                        tool_config.update(decision.suggested_parameters)
                elif decision.action == 'run' and decision.suggested_parameters:
                    tool_config.update(decision.suggested_parameters)

                logger.info(f"Starting {tool_name} (params: {decision.suggested_parameters or 'default'})")
                self.current_tool = tool_name
                self._broadcast_live_state(phase=phase_name, tool=tool_name, status='tool_running')
                self._run_tool(tool_name, tool_info['_class'], tool_config, phase_config)
                return 'success'
            except Exception as e:
                logger.error(f"Worker thread for {tool_name} failed: {e}")
                return 'failed'

        # Cap concurrency: max 3 tools simultaneously to respect the 3GB RAM limit
        max_workers = min(total, 3) if total > 0 else 1

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_tool_worker, t_info): t_info['name']
                for t_info in tools_to_run
            }
            for future in concurrent.futures.as_completed(futures):
                tool_name = futures[future]
                try:
                    result = future.result()
                    if result == 'success':
                        completed += 1
                    elif result == 'failed':
                        failed += 1
                except Exception as e:
                    logger.error(f"Unhandled exception in worker for {tool_name}: {e}")
                    failed += 1

        self.logger.phase_end(phase_name)
        logger.info(f"Phase {phase_name} complete: {completed}/{total} tools executed ({failed} failed)")

    def _run_tool(self, tool_name: str, tool_class, tool_config: dict, phase_config: dict):
        """Execute a single tool with monitoring"""
        start_time = time.time()

        try:
            # Instantiate tool
            tool = tool_class()
            tool.tag_manager = self.tag_manager

            # Budget gate: check if we can afford this tool
            budget_cfg = self.methodology.get('budget', {})
            action = budget_cfg.get('action_on_exceeded', 'abort')

            try:
                estimated = tool.estimated_requests()
                if not self.budget_tracker.within_limits(estimated):
                    msg = f"Budget would be exceeded by {tool_name} (estimated {estimated} requests)"
                    if action == 'warn':
                        logger.warning(f"{msg} — proceeding anyway (action=warn)")
                    else:
                        logger.warning(f"{msg} — skipping")
                        self.logger.tool_skipped(tool_name, "Request budget would be exceeded")
                        return
            except BudgetExceededError as e:
                if action == 'warn':
                    logger.warning(f"Budget already exceeded — proceeding anyway (action=warn): {e}")
                else:
                    logger.warning(f"Budget already exceeded — skipping {tool_name}: {e}")
                    self.logger.tool_skipped(tool_name, str(e))
                    return

            # Build resource estimate for scheduler
            estimate = self.tool_profiles.estimate_tool_resources(
                tool_name,
                tool_config.get('extra_args')
            )
            self.scheduler.register_tool_start(tool_name, estimate)

            # Prepare input files
            input_files = self._resolve_input_files(phase_config)

            # Filter input_files to only those the tool.run() method can accept
            try:
                sig = inspect.signature(tool.run)
                params = sig.parameters
                has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
                if not has_var_keyword:
                    accepted = set(name for name, p in params.items()
                                   if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                                inspect.Parameter.KEYWORD_ONLY))
                    standard_args = {'target', 'output_dir', 'tag_manager', 'config'}
                    accepted = accepted - standard_args
                    filtered_input = {k: v for k, v in input_files.items() if k in accepted}
                else:
                    filtered_input = input_files
            except (ValueError, TypeError):
                filtered_input = input_files

            # Run the tool (blocking)
            logger.info(f"Running {tool_name}...")
            result = tool.run(
                target=self.domain,
                output_dir=f"output/{self.domain}",
                tag_manager=self.tag_manager,
                config=tool_config,
                **filtered_input
            )

            # Track actual requests used
            if isinstance(result, dict):
                requests_made = result.get('requests_made', 0)
                if requests_made:
                    self.budget_tracker.add_requests(requests_made)

            # Record completion
            elapsed = time.time() - start_time
            self.logger.tool_complete(tool_name, result, elapsed)

            # Extract tags from tool output
            if hasattr(tool, 'extract_tags') and callable(tool.extract_tags):
                tags = tool.extract_tags(result)
                if tags:
                    for tag, metadata in tags.items():
                        self.tag_manager.add(tag, **metadata) if isinstance(metadata, dict) else self.tag_manager.add(tag)

            # Also call emit_tags if present
            if hasattr(tool, 'emit_tags') and callable(tool.emit_tags):
                tool.emit_tags(result, self.tag_manager)

            # Auto-emit any tags explicitly declared in the API methodology config
            explicit_tags = tool_config.get('tags_emitted', [])
            if isinstance(explicit_tags, list):
                found_something = True
                if isinstance(result, dict):
                    count = result.get('count', 1)
                    results_data = result.get('results', True)
                    if count == 0 or not results_data:
                        found_something = False
                elif isinstance(result, list):
                    if not result:
                        found_something = False
                
                if found_something:
                    for t in explicit_tags:
                        self.tag_manager.add(t, source='methodology_engine')

            # Record in history
            with self.lock:
                self.completed_tools.append({
                    'tool': tool_name,
                    'phase': self.current_phase,
                    'start_time': start_time,
                    'elapsed_seconds': elapsed,
                    'status': 'completed'
                })

            # Save checkpoint
            self.save_checkpoint()

            # Broadcast tool completion to dashboard
            completed_count = len([t for t in self.completed_tools if t.get('status') == 'completed'])
            self._broadcast_live_state(
                phase=self.current_phase, tool=tool_name, status='tool_complete',
                tools_completed=completed_count
            )

            logger.success(f"{tool_name} completed in {elapsed:.1f}s")

        except Exception as e:
            import traceback
            error_msg = f"Tool {tool_name} failed: {e}\n{traceback.format_exc()}"
            logger.error(error_msg)
            self.logger.tool_error(tool_name, e)

            # Record failure so we don't retry
            with self.lock:
                self.completed_tools.append({
                    'tool': tool_name,
                    'phase': self.current_phase,
                    'start_time': start_time,
                    'elapsed_seconds': time.time() - start_time,
                    'status': 'failed',
                    'error': str(e)
                })
            self.save_checkpoint()

        finally:
            self.budget_tracker.save_to_file(f"output/{self.domain}")
            self.scheduler.register_tool_end(tool_name)

    def _resolve_input_files(self, phase_config: dict) -> dict:
        """Resolve input file paths from processed directory"""
        input_mapping = {}
        for key, rel_path in phase_config.get('input_files', {}).items():
            if self.override_input_file and key in ['all_urls', 'parameters', 'discovered_paths']:
                full_path = Path(self.override_input_file)
            else:
                full_path = Path(f"output/{self.domain}/{rel_path}")
                
            if full_path.exists():
                input_mapping[key] = str(full_path)
            else:
                logger.warning(f"Input file not found: {full_path}")
        return input_mapping

    def _broadcast_live_state(self, phase: str = None, tool: str = None,
                               status: str = 'running', tools_completed: int = None):
        """Write live scan state to disk and update DB for dashboard consumption."""
        try:
            state = {
                'domain': self.domain,
                'scan_id': self.scan_id,
                'phase': phase or self.current_phase,
                'tool': tool or self.current_tool,
                'status': status,
                'tools_completed': tools_completed or len([t for t in self.completed_tools if t.get('status') == 'completed']),
                'tools_failed': len([t for t in self.completed_tools if t.get('status') == 'failed']),
                'tools_total': self._total_tools_count,
                'elapsed_seconds': round(time.time() - self.scan_start_time, 1) if self.scan_start_time else 0,
                'tags_count': self.tag_manager.count(),
                'budget': self.budget_tracker.get_status(),
                'timestamp': datetime.utcnow().isoformat() + 'Z'
            }

            state_path = Path(f"output/{self.domain}/live_state.json")
            state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(state_path, 'w') as f:
                json.dump(state, f, indent=2)

            # Also update the SQLite DB for dashboard polling
            if self.scan_id:
                self.scan_history.update_progress(
                    self.scan_id,
                    phase=phase or self.current_phase,
                    tool=tool or self.current_tool,
                    tools_completed=state['tools_completed'],
                    tools_total=self._total_tools_count
                )
        except Exception as e:
            logger.debug(f"Failed to broadcast live state: {e}")

    def _count_total_tools(self) -> int:
        """Count total tools across all phases for progress calculation."""
        total = 0
        phases = self.methodology.get('phases', {})
        for phase_name, phase_config in phases.items():
            if self.only_phase and phase_name != self.only_phase:
                continue
            tools = phase_config.get('tools') or phase_config.get('conditional_tools') or []
            for tool_entry in tools:
                if isinstance(tool_entry, dict):
                    tool_name = tool_entry.get('tool') or tool_entry.get('name')
                    if tool_name and tool_name in TOOL_REGISTRY:
                        if tool_entry.get('enabled', True) is not False:
                            total += 1
                elif tool_entry in TOOL_REGISTRY:
                    total += 1
        return total

    def run(self):
        """Main execution loop"""
        logger.info(f"Starting SWATH scan for {self.domain}")
        self.scan_start_time = time.time()
        self._total_tools_count = self._count_total_tools()

        # Try to resume from checkpoint
        if self.checkpoint_file:
            self.load_checkpoint()

        # Get phases from methodology
        phases = self.methodology.get('phases', {})

        # Execute all phases
        for phase_name, phase_config in phases.items():
            if self._shutdown:
                logger.warning("Shutdown requested, stopping")
                break

            # PHASE 7 SPECIAL HANDLING — human decision point ONLY for standard runs
            if phase_name == 'phase_7_vuln_scan' and not self.only_phase:
                logger.info("Phase 6 complete. Recon is done.")
                self._display_recon_summary()

                try:
                    response = input("\nContinue with vulnerability scanning (Phase 7)? [y/N]: ").strip().lower()
                except EOFError:
                    response = 'n'

                if response != 'y':
                    logger.info("Skipping Phase 7. Scan complete.")
                    break

            if self.only_phase and phase_name != self.only_phase:
                continue

            self.run_phase(phase_name, phase_config)

            # Post-phase processing: generate declared output files
            self._process_phase_outputs(phase_name, phase_config)

            # Save active tags for reporting
            self.tag_manager.save_to_file(f"output/{self.domain}")

            # Copy active_tags.json to root for report generator
            import shutil
            src = f"output/{self.domain}/processed/active_tags.json"
            dst = f"output/{self.domain}/active_tags.json"
            if os.path.exists(src):
                shutil.copy(src, dst)

            # Free memory between phases
            gc.collect()

        # Scan complete
        elapsed = time.time() - self.scan_start_time
        logger.success(f"Scan completed in {elapsed/3600:.1f} hours")

        # Final live state broadcast
        self._broadcast_live_state(status='completed')

        # Save budget status before final checkpoint
        self.budget_tracker.save_to_file(f"output/{self.domain}")

        # Final checkpoint
        self.save_checkpoint()

        # Generate summary files
        self._generate_summary()

    def _display_recon_summary(self):
        """Show user what was discovered during recon"""
        summary_path = Path(f"output/{self.domain}/processed/scan_summary.json")
        if summary_path.exists():
            with open(summary_path) as f:
                summary = json.load(f)

            print("\n" + "="*60)
            print("RECONNAISSANCE COMPLETE — DISCOVERED ASSETS")
            print("="*60)
            print(f"Target: {self.domain}")
            print(f"Subdomains found: {summary.get('subdomain_count', 0)}")
            print(f"Live hosts: {summary.get('live_host_count', 0)}")
            print(f"Technologies: {', '.join(summary.get('tech_stack', [])[:5])}")
            print(f"Endpoints discovered: {summary.get('endpoint_count', 0)}")
            print(f"Parameters found: {summary.get('parameter_count', 0)}")
            print(f"Critical tags: {', '.join(summary.get('critical_tags', []))}")
            print("="*60)
            print("\nReview the output/ directory before proceeding to Phase 7.")
            print("Vulnerability scanning can be noisy and may trigger security alerts.")
            print("="*60 + "\n")

    def _generate_summary(self):
        """Generate final scan summary"""
        summary = {
            'domain': self.domain,
            'start_time': self.scan_start_time,
            'end_time': time.time(),
            'total_duration_seconds': time.time() - self.scan_start_time,
            'tools_completed': len([t for t in self.completed_tools if t.get('status') == 'completed']),
            'tools_failed': len([t for t in self.completed_tools if t.get('status') == 'failed']),
            'final_tags': self.tag_manager.get_all(),
        }

        summary_path = Path(f"output/{self.domain}/scan_metadata.json")
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2)

        logger.info(f"Summary written to {summary_path}")

    def _process_phase_outputs(self, phase_name: str, phase_config: dict):
        """
        Post-phase processing: generate declared output files by merging tool results.
        """
        output_files = phase_config.get('output_files', {})
        if not output_files:
            return

        output_dir = Path(f"output/{self.domain}")
        processed_dir = output_dir / 'processed'
        processed_dir.mkdir(parents=True, exist_ok=True)

        # Handle live_hosts (Phase 3)
        if 'live_hosts' in output_files or 'live_hosts_txt' in output_files:
            live_hosts = []
            httpx_path = output_dir / 'raw' / 'httpx.json'
            if httpx_path.exists():
                try:
                    with open(httpx_path) as f:
                        for line in f:
                            if line.strip():
                                data = json.loads(line)
                                if 'url' in data:
                                    live_hosts.append(data['url'])
                except Exception:
                    pass
            
            if live_hosts:
                if 'live_hosts' in output_files:
                    with open(processed_dir / output_files['live_hosts'], 'w') as f:
                        json.dump(live_hosts, f)
                if 'live_hosts_txt' in output_files:
                    with open(processed_dir / output_files['live_hosts_txt'], 'w') as f:
                        f.write('\n'.join(live_hosts) + '\n')
                logger.success(f"Processed {len(live_hosts)} live hosts")

        # Handle subdomains_merged (Phase 1)
        if 'subdomains_merged' in output_files:
            merged_path = processed_dir / output_files['subdomains_merged']
            subdomains = set()

            # subfinder.txt (one per line)
            subfinder_path = output_dir / 'raw' / 'subfinder.txt'
            if subfinder_path.exists():
                try:
                    with open(subfinder_path) as f:
                        for line in f:
                            line = line.strip().lower()
                            if line and '.' in line:
                                subdomains.add(line)
                except Exception:
                    pass

            # amass.txt (one per line)
            amass_path = output_dir / 'raw' / 'amass.txt'
            if amass_path.exists():
                try:
                    with open(amass_path) as f:
                        for line in f:
                            line = line.strip().lower()
                            if line and '.' in line:
                                subdomains.add(line)
                except Exception:
                    pass

            # crtsh.json (list of strings)
            crtsh_path = output_dir / 'raw' / 'crtsh.json'
            if crtsh_path.exists():
                try:
                    with open(crtsh_path) as f:
                        data = json.load(f)
                        for entry in data:
                            if isinstance(entry, str):
                                subdomains.add(entry.strip().lower())
                except Exception:
                    pass

            # Write merged list
            if subdomains:
                with open(merged_path, 'w') as f:
                    for sub in sorted(subdomains):
                        f.write(sub + '\n')
                logger.success(f"Merged {len(subdomains)} unique subdomains -> {merged_path}")
            else:
                logger.warning("No subdomains found from Phase 1 tools")
                merged_path.write_text('')

        # Handle all_urls (Phase 5) — merge katana, gau, paramspider outputs
        if 'all_urls' in output_files:
            all_urls = set()
            url_sources = ['katana.txt', 'gau.txt', 'paramspider.txt']
            for src_file in url_sources:
                src_path = output_dir / 'raw' / src_file
                if src_path.exists():
                    try:
                        with open(src_path) as f:
                            for line in f:
                                line = line.strip()
                                if line and line.startswith('http'):
                                    all_urls.add(line)
                    except Exception:
                        pass

            merged_urls_path = processed_dir / output_files['all_urls']
            if all_urls:
                with open(merged_urls_path, 'w') as f:
                    for url in sorted(all_urls):
                        f.write(url + '\n')
                logger.success(f"Merged {len(all_urls)} unique URLs -> {merged_urls_path}")
            else:
                logger.warning("No URLs found from Phase 5 tools")
                merged_urls_path.write_text('')

        # Handle parameters (Phase 5) — merge paramspider results
        if 'parameters' in output_files:
            params = []
            # Paramspider output is usually a list of URLs with parameters
            paramspider_path = output_dir / 'raw' / 'paramspider.txt'
            if paramspider_path.exists():
                try:
                    with open(paramspider_path) as f:
                        for line in f:
                            line = line.strip()
                            if line and '?' in line:
                                params.append(line)
                except Exception:
                    pass
            
            # Katana can also find parameters (JC=true)
            katana_path = output_dir / 'raw' / 'katana.txt'
            if katana_path.exists():
                try:
                    with open(katana_path) as f:
                        for line in f:
                            line = line.strip()
                            if line and '?' in line and line not in params:
                                params.append(line)
                except Exception:
                    pass

            if params:
                param_out_path = processed_dir / output_files['parameters']
                with open(param_out_path, 'w') as f:
                    json.dump(params, f, indent=2)
                logger.success(f"Merged {len(params)} URLs with parameters -> {param_out_path}")
                # Set tag if parameters found
                self.tag_manager.add('params_found', source='orchestrator', confidence='medium')
            else:
                (processed_dir / output_files['parameters']).write_text('[]')

        # Handle discovered_paths / interesting_paths (Phase 6) — parse ffuf and merge to all_urls
        if 'discovered_paths' in output_files or 'interesting_paths' in output_files:
            ffuf_paths = set()
            ffuf_path = output_dir / 'raw' / 'ffuf.json'
            if ffuf_path.exists():
                try:
                    with open(ffuf_path) as f:
                        data = json.load(f)
                        for result in data.get('results', []):
                            if 'url' in result:
                                ffuf_paths.add(result['url'])
                except Exception:
                    pass

            if ffuf_paths:
                if 'discovered_paths' in output_files:
                    with open(processed_dir / output_files['discovered_paths'], 'w') as f:
                        json.dump(list(ffuf_paths), f, indent=2)
                
                if 'interesting_paths' in output_files:
                    with open(processed_dir / output_files['interesting_paths'], 'w') as f:
                        f.write('\n'.join(sorted(ffuf_paths)) + '\n')
                        
                # Aggressively append these directly to all_urls.txt so Phase 7 scanners hit them!
                all_urls_path = processed_dir / 'all_urls.txt'
                if all_urls_path.exists():
                    existing_urls = set()
                    try:
                        with open(all_urls_path) as f:
                            existing_urls = set(line.strip() for line in f if line.strip())
                    except Exception:
                        pass
                    
                    new_urls = ffuf_paths - existing_urls
                    if new_urls:
                        with open(all_urls_path, 'a') as f:
                            f.write('\n' + '\n'.join(sorted(new_urls)) + '\n')
                        logger.success(f"Injected {len(new_urls)} FFuF paths into all_urls.txt for vuln scanning")

                logger.success(f"Processed {len(ffuf_paths)} directory paths from FFuF")


def main():
    """CLI entry point for v2 orchestrator"""
    import argparse

    parser = argparse.ArgumentParser(description="SWATH Orchestrator V2 (Resource-Aware)")
    parser.add_argument("domain", help="Target domain")
    parser.add_argument("--methodology", default="config/default_methodology.yaml",
                       help="Path to methodology YAML")
    parser.add_argument("--no-checkpoint", action="store_true",
                       help="Disable checkpoint/resume")
    parser.add_argument("--checkpoint-file",
                       help="Path to checkpoint file (default: output/{domain}/checkpoint.json)")

    args = parser.parse_args()

    orch = OrchestratorV2(
        domain=args.domain,
        methodology_path=args.methodology,
        checkpoint_file=None if args.no_checkpoint else (args.checkpoint_file or None),
        adaptive=True
    )

    try:
        orch.run()
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
    except Exception as e:
        logger.error(f"Scan failed: {e}")
        raise


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}")
    main()
