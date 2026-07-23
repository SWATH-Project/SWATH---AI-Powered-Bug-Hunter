import cmd
import sys
import os
from rich.console import Console as RichConsole
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt
from core.database import Database

class SwathConsole(cmd.Cmd):
    intro = ""
    
    def __init__(self):
        super().__init__()
        self.rich_console = RichConsole()
        self.target = None
        self.update_prompt()
        self.db = Database()
        
        # Display Banner
        banner = """
  ╔═══════════════════════════════════════╗
  ║        SWATH v2.0 — HuntForge        ║
  ║   AI-Powered Bug Bounty Framework    ║
  ╚═══════════════════════════════════════╝
        """
        self.rich_console.print(Panel(banner, style="bold blue"))

    def update_prompt(self):
        if self.target:
            self.prompt = f"swath ({self.target}) > "
        else:
            self.prompt = "swath > "

    def do_use(self, arg):
        """Set current target domain: use <domain>"""
        if not arg:
            self.rich_console.print("[red]Usage: use <domain>[/red]")
            return
        self.target = arg
        self.db.upsert_target(self.target)
        self.rich_console.print(f"[*] Target set: [green]{self.target}[/green]")
        self.update_prompt()

    def do_targets(self, arg):
        """List all known targets from database"""
        conn = self.db._get_conn()
        cur = conn.execute('SELECT domain, program, first_seen, last_scanned FROM targets')
        rows = cur.fetchall()
        conn.close()
        
        table = Table(title="Known Targets")
        table.add_column("Domain", style="cyan")
        table.add_column("Program", style="magenta")
        table.add_column("First Seen")
        table.add_column("Last Scanned")
        
        for row in rows:
            table.add_row(row['domain'], str(row['program']), str(row['first_seen']), str(row['last_scanned']))
            
        self.rich_console.print(table)

    def do_scan(self, arg):
        """Launch scan with current settings: scan <quick|full>"""
        if not self.target:
            self.rich_console.print("[red]Please set a target first using 'use <domain>'[/red]")
            return
            
        methodology = "config/workflows/quick_wins.yaml"
        if arg == "full":
            methodology = "config/workflows/full_assault.yaml"
            
        self.rich_console.print(f"[*] Launching SWATH scan for {self.target} using {methodology}...")
        from core.orchestrator_v2 import OrchestratorV2
        import threading
        
        def run_scan_thread():
            try:
                orch = OrchestratorV2(self.target, methodology_path=methodology)
                orch.run()
                self.rich_console.print(f"\n[green][✔] Scan completed for {self.target}[/green]")
            except Exception as e:
                self.rich_console.print(f"\n[red][✖] Scan failed: {e}[/red]")
            self.update_prompt()
            
        t = threading.Thread(target=run_scan_thread, daemon=True)
        t.start()
        self.rich_console.print("[yellow]Scan running in background. You can continue using the console.[/yellow]")

    def do_findings(self, arg):
        """List all findings for current target"""
        if not self.target:
            self.rich_console.print("[red]Please set a target first.[/red]")
            return
            
        conn = self.db._get_conn()
        cur = conn.execute('''
            SELECT f.severity, f.type, f.title, f.is_reported 
            FROM findings f 
            JOIN targets t ON f.target_id = t.id 
            WHERE t.domain = ?
        ''', (self.target,))
        rows = cur.fetchall()
        conn.close()
        
        if not rows:
            self.rich_console.print("No findings yet.")
            return
            
        table = Table(title=f"Findings for {self.target}")
        table.add_column("Severity")
        table.add_column("Type")
        table.add_column("Title")
        table.add_column("Reported")
        
        for row in rows:
            sev = row['severity'].upper()
            color = "white"
            if sev == 'CRITICAL': color = "red bold"
            elif sev == 'HIGH': color = "red"
            elif sev == 'MEDIUM': color = "yellow"
            elif sev == 'LOW': color = "cyan"
            
            table.add_row(f"[{color}]{sev}[/{color}]", row['type'], row['title'], "Yes" if row['is_reported'] else "No")
            
        self.rich_console.print(table)

    def do_exit(self, arg):
        """Exit the console"""
        self.rich_console.print("Exiting...")
        return True
        
    do_quit = do_exit
    do_EOF = do_exit
        
    def do_clear(self, arg):
        """Clear screen"""
        os.system('cls' if os.name == 'nt' else 'clear')

    def do_modules(self, arg):
        """List all loaded modules"""
        from core.plugin_loader import PluginLoader
        modules = PluginLoader.list_by_phase()
        for phase, mods in modules.items():
            self.rich_console.print(f"[bold cyan]Phase: {phase}[/bold cyan]")
            for m in mods:
                self.rich_console.print(f"  - {m}")
                
    def do_assets(self, arg):
        """List all assets for current target"""
        if not self.target:
            self.rich_console.print("[red]Please set a target first.[/red]")
            return
        conn = self.db._get_conn()
        cur = conn.execute('SELECT a.type, a.value, a.source FROM assets a JOIN targets t ON a.target_id = t.id WHERE t.domain = ?', (self.target,))
        rows = cur.fetchall()
        conn.close()
        table = Table(title=f"Assets for {self.target}")
        table.add_column("Type")
        table.add_column("Value")
        table.add_column("Source")
        for r in rows:
            table.add_row(r['type'], r['value'], r['source'])
        self.rich_console.print(table)
        
    def do_export(self, arg):
        """Export findings: export <json|csv|markdown|hackerone>"""
        if not self.target:
            self.rich_console.print("[red]Please set a target first.[/red]")
            return
        if not arg:
            self.rich_console.print("[red]Usage: export <format>[/red]")
            return
            
        from core.exporter import Exporter
        exporter = Exporter()
        
        conn = self.db._get_conn()
        cur = conn.execute('SELECT f.*, a.value as asset_value FROM findings f JOIN targets t ON f.target_id = t.id LEFT JOIN assets a ON f.asset_id = a.id WHERE t.domain = ?', (self.target,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        
        out_file = f"export_{self.target}.{arg}"
        exporter.export(rows, arg, out_file)
        self.rich_console.print(f"[green]Exported {len(rows)} findings to {out_file}[/green]")

if __name__ == '__main__':
    try:
        SwathConsole().cmdloop()
    except KeyboardInterrupt:
        print("\nExiting...")
