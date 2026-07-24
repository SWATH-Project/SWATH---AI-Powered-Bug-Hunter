#!/usr/bin/env python3
"""
SWATH CLI Entry Point

Responsibilities:
- Parse CLI arguments
- Display Rich terminal UI
- Handle AI prompt → methodology generation
- Load configuration
- Validate scope
- Initialize orchestrator
- Launch scans
"""

import os
import sys
import argparse
import yaml
import json
import subprocess
from pathlib import Path
from dotenv import load_dotenv

# Auto-load .env from project root on every CLI invocation
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# Force UTF-8 output on Windows to avoid Rich Unicode crashes
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from core.orchestrator_v2 import OrchestratorV2 as Orchestrator
from core.scope_enforcer import ScopeEnforcer
from core.scan_history import ScanHistory
from core.tag_manager import TagManager

from ai.methodology_engine import generate_methodology
from ai.report_generator import generate_report as ai_generate_report

# --------------------------------------------------------
# Global Console
# --------------------------------------------------------

console = Console(force_terminal=True)

# --------------------------------------------------------
# Constants
# --------------------------------------------------------

DEFAULT_METHOD = "config/default_methodology.yaml"

# --------------------------------------------------------
# UI Components
# --------------------------------------------------------

def print_banner():
    console.print(
        Panel.fit(
            "[bold cyan]SWATH v1.0[/bold cyan]\n"
            "[dim]AI-Powered Bug Bounty Recon Framework[/dim]",
            border_style="cyan"
        )
    )


def print_scan_summary(domain, methodology):
    table = Table(title="Scan Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Target", domain)
    table.add_row("Methodology", methodology)
    console.print(table)


# --------------------------------------------------------
# AI Methodology Generation
# --------------------------------------------------------

def handle_ai_prompt(prompt):
    console.print("\n[yellow]Generating methodology using AI...[/yellow]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}")
    ) as progress:
        progress.add_task("Contacting AI engine...", total=None)
        method = generate_methodology(prompt)

    if not method:
        console.print("[red]AI failed to generate methodology[/red]")
        sys.exit(1)

    output_path = "config/generated_methodology.yaml"
    os.makedirs("config", exist_ok=True)

    with open(output_path, "w") as f:
        yaml.dump(method, f)

    console.print(
        f"[green]Methodology generated:[/green] {output_path}"
    )


# --------------------------------------------------------
# Scan Execution
# --------------------------------------------------------

def run_scan(domain, methodology_path, only_phase=None, override_input_file=None):
    if not os.path.exists(methodology_path):
        console.print(
            f"[red]Methodology file not found:[/red] {methodology_path}"
        )
        sys.exit(1)

    print_scan_summary(domain, methodology_path)

    # Scope Enforcement
    scope = ScopeEnforcer()
    allowed, reason, program = scope.check(domain)

    if not allowed:
        console.print(
            f"[red]Target blocked by scope enforcement[/red]\n"
            f"Reason: {reason}"
        )
        console.print(
            "\n[yellow]If you own this domain you must manually approve it.[/yellow]"
        )
        confirm = console.input(
            f"Type '{domain}' to confirm ownership: "
        )
        if not scope.approve_manual(domain, confirm):
            console.print("[red]Approval failed. Aborting.[/red]")
            sys.exit(1)
    else:
        console.print(
            f"[green]Scope check passed[/green] ({reason})"
        )

    # Start Scan
    console.print("\n[cyan]Launching SWATH Orchestrator[/cyan]\n")

    history = ScanHistory()
    output_dir = os.path.abspath(f"output/{domain}")
    scan_id = history.record_start(domain, output_dir)
    status = "FAILED"

    try:
        orch = Orchestrator(
            domain=domain,
            methodology_path=methodology_path,
            only_phase=only_phase,
            override_input_file=override_input_file,
            scan_id=scan_id
        )
        orch.run()
        status = "COMPLETED"

    except KeyboardInterrupt:
        console.print("\n[yellow]Scan interrupted by user[/yellow]")
        status = "INTERRUPTED"

    except FileNotFoundError as e:
        console.print(
            f"\n[red]Required binary not found:[/red] {e}\n"
            "[yellow]Ensure all required tools are installed and on PATH.[/yellow]"
        )
        status = "FAILED"

    except ImportError as e:
        console.print(
            f"\n[red]Import error:[/red] {e}\n"
            "[yellow]A required Python module is missing. Check that all dependencies are installed "
            "(e.g. wappalyzer.py, python-dotenv, etc.).[/yellow]"
        )
        status = "FAILED"

    except subprocess.CalledProcessError as e:
        if "docker" in str(e).lower():
            console.print(
                "\n[red]Docker error:[/red] A container command failed.\n"
                "[yellow]Is Docker running? Try 'docker info' to verify.[/yellow]"
            )
        else:
            console.print(f"\n[red]Subprocess error:[/red] {e}")
        status = "FAILED"

    except Exception as e:
        err_msg = str(e).lower()
        if "docker" in err_msg and ("daemon" in err_msg or "connect" in err_msg):
            console.print(
                "\n[red]Docker does not appear to be running.[/red]\n"
                "[yellow]Start Docker Desktop or the Docker daemon and try again.[/yellow]"
            )
        else:
            console.print(
                f"\n[red]Fatal error:[/red] {e}"
            )
        status = "FAILED"

    finally:
        tag_count = len(orch.tag_manager.get_all()) if 'orch' in locals() else 0
        history.record_end(scan_id, status, tag_count)

    # Auto-generate AI report on successful completion
    if status == "COMPLETED":
        console.print("\n[cyan]━━━ Auto-Generating AI Report ━━━[/cyan]")
        try:
            generate_report(domain)
        except Exception as e:
            console.print(f"[yellow]Report generation skipped:[/yellow] {e}")


# --------------------------------------------------------
# Report Generator
# --------------------------------------------------------

def generate_report(domain):
    output_dir = f"output/{domain}"
    tags_file = os.path.join(output_dir, "active_tags.json")

    if not os.path.exists(tags_file):
        console.print("[red]No scan data found for this domain[/red]")
        return

    # Reconstruct TagManager state
    tm = TagManager()
    with open(tags_file, 'r') as f:
        tm.tags = json.load(f)

    console.print("[cyan]Generating executive report via OpenRouter...[/cyan]")
    ai_generate_report(domain, tm, output_dir)

    report_path = os.path.join(output_dir, 'logs', 'ai_report.md')
    if os.path.exists(report_path):
        console.print(f"[green]Report ready:[/green] {report_path}")


# --------------------------------------------------------
# Resume Scan
# --------------------------------------------------------

def resume_scan(domain):
    console.print(f"[cyan]Resuming scan for {domain}[/cyan]")

    methodology_path = DEFAULT_METHOD
    if not os.path.exists(methodology_path):
        console.print(f"[red]Methodology file not found:[/red] {methodology_path}")
        sys.exit(1)

    checkpoint_file = f"output/{domain}/checkpoint.json"
    if not os.path.exists(checkpoint_file):
        console.print("[red]No checkpoint found. Start a new scan instead.[/red]")
        sys.exit(1)

    try:
        with open(checkpoint_file, "r") as f:
            checkpoint_data = json.load(f)
        saved_methodology = checkpoint_data.get("methodology_path")
        if saved_methodology and saved_methodology != methodology_path:
            console.print(
                f"[yellow]Warning:[/yellow] Checkpoint was created with methodology "
                f"'{saved_methodology}', but resuming with '{methodology_path}'. "
                f"Results may be inconsistent."
            )
    except (json.JSONDecodeError, KeyError):
        console.print(
            "[yellow]Warning:[/yellow] Could not read methodology from checkpoint. "
            "Proceeding with default methodology."
        )

    history = ScanHistory()
    output_dir = os.path.abspath(f"output/{domain}")
    scan_id = history.record_start(domain, output_dir)
    status = "FAILED"

    try:
        orch = Orchestrator(
            domain=domain,
            methodology_path=methodology_path,
            checkpoint_file=checkpoint_file,
            scan_id=scan_id
        )
        orch.run()
        status = "COMPLETED"

    except KeyboardInterrupt:
        console.print("\n[yellow]Scan interrupted by user[/yellow]")
        status = "INTERRUPTED"

    except FileNotFoundError as e:
        console.print(
            f"\n[red]Required binary not found:[/red] {e}\n"
            "[yellow]Ensure all required tools are installed and on PATH.[/yellow]"
        )
        status = "FAILED"

    except ImportError as e:
        console.print(
            f"\n[red]Import error:[/red] {e}\n"
            "[yellow]A required Python module is missing. Check that all dependencies are installed "
            "(e.g. wappalyzer.py, python-dotenv, etc.).[/yellow]"
        )
        status = "FAILED"

    except subprocess.CalledProcessError as e:
        if "docker" in str(e).lower():
            console.print(
                "\n[red]Docker error:[/red] A container command failed.\n"
                "[yellow]Is Docker running? Try 'docker info' to verify.[/yellow]"
            )
        else:
            console.print(f"\n[red]Subprocess error:[/red] {e}")
        status = "FAILED"

    except Exception as e:
        err_msg = str(e).lower()
        if "docker" in err_msg and ("daemon" in err_msg or "connect" in err_msg):
            console.print(
                "\n[red]Docker does not appear to be running.[/red]\n"
                "[yellow]Start Docker Desktop or the Docker daemon and try again.[/yellow]"
            )
        else:
            console.print(f"\n[red]Fatal error:[/red] {e}")
        status = "FAILED"

    finally:
        tag_count = len(orch.tag_manager.get_all()) if 'orch' in locals() else 0
        history.record_end(scan_id, status, tag_count)


def launch_console():
    from core.console import SwathConsole
    try:
        SwathConsole().cmdloop()
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)

def run_monitor(domain):
    from core.monitor import MonitorManager
    from core.database import Database
    console.print(f"[*] Running monitor check for {domain}...")
    db = Database()
    monitor = MonitorManager(db)
    monitor.run_monitor_check(domain)
    console.print(f"[green]Monitor check completed for {domain}.[/green]")

# --------------------------------------------------------
# Argument Parsing
# --------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="swath",
        description="AI Powered Bug Bounty Recon Framework"
    )

    subparsers = parser.add_subparsers(dest="command")

    # scan
    scan = subparsers.add_parser("scan", help="Run a reconnaissance scan")
    scan.add_argument("domain", help="Target domain")
    scan.add_argument(
        "--methodology",
        default=DEFAULT_METHOD,
        help="Path to YAML methodology file"
    )

    # precision
    precision = subparsers.add_parser("precision", help="Run a precision vulnerability strike")
    precision.add_argument("domain")
    precision.add_argument("--file", required=True, help="Target list file override")

    # ai
    ai = subparsers.add_parser("ai", help="Generate scan methodology using AI")
    ai.add_argument("prompt", help="Instruction prompt for AI")

    # report
    report = subparsers.add_parser("report", help="Generate executive AI report")
    report.add_argument("domain")

    # resume
    resume = subparsers.add_parser("resume", help="Resume a previous scan")
    resume.add_argument("domain")

    # dashboard
    dash = subparsers.add_parser("dashboard", help="Launch the SWATH web dashboard")
    dash.add_argument(
        "--port",
        default=5000,
        type=int,
        help="Port to run the dashboard on (default: 5000)"
    )
    
    # 5) "interactive" - Launch REPL console
    parser_interactive = subparsers.add_parser(
        "interactive",
        help="Launch the interactive Metasploit-style console"
    )

    # 6) "monitor" - Run continuous monitoring
    parser_monitor = subparsers.add_parser(
        "monitor",
        help="Run continuous monitoring checks"
    )
    parser_monitor.add_argument("domain", nargs="?", default="all", help="Target domain or 'all'")

    return parser


# --------------------------------------------------------
# Main Entry
# --------------------------------------------------------

def main():
    parser = build_parser()
    args = parser.parse_args()

    print_banner()

    if not args.command:
        parser.print_help()
        return

    if args.command == "interactive":
        launch_console()
        return

    if args.command == "monitor":
        run_monitor(args.domain)
        return

    if args.command == "scan":
        run_scan(
            domain=args.domain,
            methodology_path=args.methodology,
        )
        
    elif args.command == "precision":
        run_scan(
            domain=args.domain,
            methodology_path=DEFAULT_METHOD,
            only_phase="phase_7_vuln_scan",
            override_input_file=args.file,
        )

    elif args.command == "ai":
        handle_ai_prompt(prompt=args.prompt)

    elif args.command == "report":
        generate_report(domain=args.domain)

    elif args.command == "resume":
        resume_scan(domain=args.domain)

    elif args.command == "dashboard":
        console.print(
            f"[cyan]Starting SWATH Dashboard on http://localhost:{args.port}[/cyan]"
        )
        dashboard_env = {**os.environ, "FLASK_RUN_PORT": str(args.port)}
        proc = subprocess.Popen(
            [sys.executable, "-m", "flask", "run", "--host=0.0.0.0", f"--port={args.port}"],
            cwd=os.path.join(os.path.dirname(__file__), "dashboard"),
            env=dashboard_env,
        )
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            console.print("\n[yellow]Dashboard stopped.[/yellow]")


# --------------------------------------------------------

if __name__ == "__main__":
    main()