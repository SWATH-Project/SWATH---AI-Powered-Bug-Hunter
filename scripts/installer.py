#!/usr/bin/env python3
"""
HuntForge Native Installer

Detects OS, checks installed tools, installs missing ones.
Works on: Kali, Ubuntu, Debian, macOS, Windows WSL2

Usage:
  python3 scripts/installer.py --profile professional
  python3 scripts/installer.py --download-wordlists
"""

import os
import sys
import json
import subprocess
import shutil
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# Add common tool paths to environment
HOME = str(Path.home())
EXTRA_PATHS = [
    f"{HOME}/.local/bin",
    f"{HOME}/go/bin",
    "/go/bin",
    "/usr/local/go/bin"
]
for p in EXTRA_PATHS:
    if os.path.exists(p) and p not in os.environ["PATH"]:
        os.environ["PATH"] = f"{p}:{os.environ['PATH']}"

# Color codes for terminal output
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
BLUE = '\033[94m'
RESET = '\033[0m'
BOLD = '\033[1m'

def log_info(msg):
    print(f"{BLUE}[*]{RESET} {msg}")

def log_success(msg):
    print(f"{GREEN}[+]{RESET} {msg}")

def log_warning(msg):
    print(f"{YELLOW}[!]{RESET} {msg}")

def log_error(msg):
    print(f"{RED}[-]{RESET} {msg}")

def run_cmd(cmd: List[str], check=True, capture_output=False) -> Tuple[bool, str]:
    """Run shell command and return (success, output)"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            timeout=900
        )
        if check and result.returncode != 0:
            return False, result.stderr
        return True, result.stdout if capture_output else ""
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)

def detect_os() -> Tuple[str, str]:
    """Detect OS and package manager. Returns (os_family, package_manager)"""
    platform = sys.platform

    if platform.startswith('linux'):
        # Check for Kali/Ubuntu/Debian
        if shutil.which('apt'):
            return 'debian', 'apt'
        elif shutil.which('yum'):
            return 'redhat', 'yum'
        elif shutil.which('dnf'):
            return 'redhat', 'dnf'
        elif shutil.which('pacman'):
            return 'arch', 'pacman'
        return 'linux', 'unknown'

    elif platform == 'darwin':
        return 'macos', 'brew'

    elif platform == 'win32':
        # WSL2?
        if 'microsoft' in os.uname().release.lower():
            return 'wsl', 'apt'  # WSL uses apt
        return 'windows', 'unknown'

    return 'unknown', 'unknown'

def check_tool_installed(tool_name: str) -> bool:
    """Check if a tool binary is in PATH"""
    return shutil.which(tool_name) is not None

def install_via_apt(tool: str, package_name: str = None) -> bool:
    """Install tool using apt (Kali/Ubuntu/Debian)"""
    package = package_name or tool
    log_info(f"Installing {tool} via apt...")

    # Handle permissions
    is_root = os.geteuid() == 0 if hasattr(os, 'geteuid') else False
    sudo_cmd = ["sudo"] if not is_root and shutil.which("sudo") else []

    # Update package list (if not done recently)
    run_cmd(sudo_cmd + ['apt-get', 'update'], check=False)

    success, output = run_cmd(sudo_cmd + ['apt-get', 'install', '-y', package], capture_output=True)
    if success:
        log_success(f"{tool} installed")
        return True
    else:
        log_error(f"Failed to install {tool}: {output}")
        if not is_root and not sudo_cmd:
            log_warning("Hint: Run with '-u root' in docker exec to allow system installations")
        return False

def install_via_go(tool: str, go_package: str) -> bool:
    """Install Go tool via go install"""
    log_info(f"Installing {tool} via go install...")

    # Check if Go is installed
    if not check_tool_installed('go'):
        log_error("Go is not installed. Install Go first or use apt/other method.")
        return False

    success, output = run_cmd(['go', 'install', go_package], capture_output=True)
    if success:
        log_success(f"{tool} installed to ~/go/bin")
        return True
    else:
        log_error(f"Failed to install {tool}: {output}")
        return False

def install_via_pip(tool: str, package_name: str = None) -> bool:
    """Install Python tool via pip, handling Kali's PEP 668 restrictions"""
    package = package_name or tool
    log_info(f"Installing {tool} via pip...")

    # Check for existing virtual environment (Docker container has one at /home/huntforge/venv)
    possible_venvs = [
        Path('.') / 'venv',
        Path.home() / '.huntforge' / 'venv',
        Path('/home/huntforge/venv'),
    ]
    venv_pip = None
    for venv in possible_venvs:
        pip_path = venv / 'bin' / 'pip'
        if pip_path.exists():
            venv_pip = str(pip_path)
            break

    if venv_pip:
        # Use virtual environment's pip (no special flags needed)
        pip_cmd = [venv_pip, 'install', package]
    else:
        # Using system Python - handle Kali's externally managed environment
        is_root = os.geteuid() == 0 if hasattr(os, 'geteuid') else False
        pip_cmd = [sys.executable, '-m', 'pip', 'install']

        # On Kali 2024+, need --break-system-packages for system Python
        # or use --user for user installs (though still may be blocked)
        if os.path.exists('/etc/kali-release'):
            if is_root:
                pip_cmd.append('--break-system-packages')
            else:
                # Non-root on Kali: --user may still be blocked; try anyway
                pip_cmd.append('--user')
        else:
            # Non-Kali: use --user for non-root to avoid system modification
            if not is_root:
                pip_cmd.append('--user')

        pip_cmd.append(package)

    success, output = run_cmd(pip_cmd, capture_output=True)
    if success:
        log_success(f"{tool} installed")
        return True
    else:
        log_error(f"Failed to install {tool}: {output}")
        # Provide specific guidance for common errors
        if output and "externally-managed-environment" in output:
            log_warning("Kali's PEP 668 protection blocked the install.")
            log_warning("Solutions:")
            log_warning("  1. Run installer as root: docker exec -u root huntforge-kali ./scripts/installer.py")
            log_warning("  2. Create virtualenv: python3 -m venv ~/.huntforge/venv")
            log_warning("  3. Install from Kali repos: apt install python3-" + tool)
        return False

def install_via_cargo(tool: str, package_name: str = None) -> bool:
    """Install Rust tool via cargo"""
    package = package_name or tool
    log_info(f"Installing {tool} via cargo...")

    # Check if cargo is installed
    if not check_tool_installed('cargo'):
        log_error("cargo (Rust) is not installed. Install Rust first or use apt/other method.")
        return False

    success, output = run_cmd(['cargo', 'install', package], capture_output=True)
    if success:
        log_success(f"{tool} installed to ~/.cargo/bin")
        return True
    else:
        log_error(f"Failed to install {tool}: {output}")
        return False

def ensure_git_binaries():
    """Ensure core Git binaries are in PATH"""
    home = Path.home()
    go_bin = home / 'go' / 'bin'
    local_bin = home / '.local' / 'bin'

    paths_to_add = []
    if go_bin.exists():
        paths_to_add.append(str(go_bin))
    if local_bin.exists():
        paths_to_add.append(str(local_bin))

    if paths_to_add:
        current_path = os.environ.get('PATH', '')
        for path in paths_to_add:
            if path not in current_path:
                os.environ['PATH'] = f"{path}:{current_path}"
                log_info(f"Added {path} to PATH for this session")

class HuntForgeInstaller:
    """Main installer logic"""

    # Tool installation strategies by OS
    TOOL_INSTALL_MAP = {
        'debian': {  # Kali, Ubuntu, Debian
            # Phase 1 - Passive Recon
            'subfinder': ('apt', 'subfinder'),
            'amass': ('apt', 'amass'),
            'assetfinder': ('go', 'github.com/tomnomnom/assetfinder@latest'),
            'theharvester': ('apt', 'theharvester'),
            'findomain': ('apt', 'findomain'),
            'waybackurls': ('go', 'github.com/tomnomnom/waybackurls@latest'),
            'crtsh': None,  # API call, no binary needed
            'chaos': ('go', 'github.com/projectdiscovery/chaos-client/cmd/chaos@latest'),

            # Phase 2 - Secrets
            'gitleaks': ('apt', 'gitleaks'),
            'trufflehog': ('apt', 'trufflehog'),
            'github_dorking': None,  # Python module, handled separately
            'jsluice': ('go', 'github.com/BishopFox/jsluice/cmd/jsluice@latest'),
            'secretfinder': ('pip', 'secretfinder'),
            'linkfinder': ('pip', 'linkfinder'),

            # Phase 3 - Discovery
            'httpx': ('go', 'github.com/projectdiscovery/httpx/cmd/httpx@latest'),
            'dnsx': ('apt', 'dnsx'),
            'naabu': ('apt', 'naabu'),
            'puredns': ('go', 'github.com/d3mondev/puredns/cmd/puredns@latest'),
            'gowitness': ('go', 'github.com/sensepost/gowitness@latest'),
            'asnmap': ('go', 'github.com/projectdiscovery/asnmap/cmd/asnmap@latest'),

            # Phase 4 - Surface Intel
            'whatweb': ('apt', 'whatweb'),
            'wappalyzer_cli': None,  # Private repo - install manually if needed
            'nmap_service': ('apt', 'nmap'),
            'shodan_cli': ('apt', 'shodan'),
            'censys_cli': ('pip', 'censys'),

            # Phase 5 - Enumeration
            'katana': ('go', 'github.com/projectdiscovery/katana/cmd/katana@latest'),
            'gau': ('go', 'github.com/lc/gau/v2/cmd/gau@latest'),
            'gospider': ('go', 'github.com/jaeles-project/gospider@latest'),
            'paramspider': None,  # Private repo - manual installation required
            'gf_extract': None,  # Part of gf (install gf separately if needed)
            'graphql_voyager': None,  # Built-in module, part of HuntForge
            'arjun': ('pip', 'arjun'),

            # Phase 6 - Content Discovery
            'ffuf': ('apt', 'ffuf'),
            'dirsearch': ('apt', 'dirsearch'),
            'feroxbuster': ('apt', 'feroxbuster'),  # Try apt first, fallback to cargo if needed
            'wpscan': ('apt', 'wpscan'),
            's3scanner': ('pip', 's3scanner'),
            'cloud_enum': ('pip', 'cloud-enum'),

            # Phase 7 - Vuln Scan
            'nuclei': ('apt', 'nuclei'),
            'subjack': ('go', 'github.com/haccer/subjack@latest'),
            'nikto': ('apt', 'nikto'),
            'dalfox': ('go', 'github.com/hahwul/dalfox/v2@latest'),
            'sqlmap': ('apt', 'sqlmap'),
            # Aliases - these don't need separate installation:
            # nuclei_cms: uses 'nuclei' binary with different templates
            # nuclei_auth: uses 'nuclei' binary with different templates
            # wpscan_vuln: uses 'wpscan' binary with different flags
            # cors_scanner: custom Python module
            # ssrf_check: custom Python module or nuclei template
        },
        'macos': {  # macOS with Homebrew
            'subfinder': ('brew', 'subfinder'),
            'httpx': ('brew', 'httpx'),
            'nuclei': ('brew', 'nuclei'),
            'ffuf': ('brew', 'ffuf'),
            'sqlmap': ('brew', 'sqlmap'),
            # Others may need go install
        },
        'wsl': {  # WSL2 - same as Debian
            'subfinder': ('apt', 'subfinder'),
            'httpx': ('apt', 'httpx'),
            'nuclei': ('apt', 'nuclei'),
            'ffuf': ('apt', 'ffuf'),
            'sqlmap': ('apt', 'sqlmap'),
        }
    }

    # TOOLS: What real bug bounty hunters actually use (16 tools)
    PROFESSIONAL_TOOLS = [
        # Phase 1 - Passive Recon (2 tools)
        'subfinder', 'crtsh',

        # Phase 2 - Secrets (2 tools)
        'gitleaks', 'trufflehog',

        # Phase 3 - Live Discovery (2 tools)
        'httpx', 'naabu',

        # Phase 4 - Surface Intel (1 tool)
        'whatweb',

        # Phase 5 - Enumeration (3 tools)
        'katana', 'gau', 'arjun',

        # Phase 6 - Content Discovery (2 tools)
        'ffuf', 'wpscan',

        # Phase 7 - Vuln Scan (3 tools)
        'nuclei', 'subjack', 'dalfox',
    ]

    # Profile to tools mapping
    PROFILE_TOOLS = {
        'professional': PROFESSIONAL_TOOLS,
    }

    def __init__(self, profile: str = 'professional', download_wordlists: bool = False):
        self.profile = profile
        self.download_wordlists = download_wordlists
        self.os_family, self.pkg_manager = detect_os()
        self.home = Path.home()
        self.huntforge_dir = self.home / '.huntforge'
        self.config_dir = self.huntforge_dir / 'config'
        self.wordlists_dir = self.huntforge_dir / 'wordlists'
        self.installed_tools: Dict[str, bool] = {}

        log_info(f"Detected OS: {self.os_family}, Package manager: {self.pkg_manager}")
        log_info(f"Profile: {profile}")
        log_info(f"HuntForge dir: {self.huntforge_dir}")

    def check_prerequisites(self) -> bool:
        """Check if system meets basic requirements"""
        log_info("Checking prerequisites...")

        # Python 3.9+
        if sys.version_info < (3, 9):
            log_error("Python 3.9+ required")
            return False
        log_success(f"Python {sys.version_info.major}.{sys.version_info.minor} OK")

        # pip
        if not check_tool_installed('pip'):
            log_warning("pip not found, will install via apt")
            if self.os_family == 'debian':
                install_via_apt('pip3', 'python3-pip')
            else:
                log_error("Cannot install pip automatically. Please install pip first.")
                return False

        # Check if running on Kali (recommended) or compatible
        if self.os_family not in ['debian', 'macos', 'wsl']:
            log_warning(f"Unsupported OS: {self.os_family}. Installation may fail.")
            return False

        return True

    def check_existing_tools(self, tools: List[str]) -> Dict[str, bool]:
        """Check which tools are already installed"""
        log_info(f"Checking {len(tools)} tools for profile '{self.profile}'...")

        # Tools that are Python modules (check via import)
        python_modules = [
            'crtsh', 'graphql_voyager', 's3scanner', 'cloud_enum',
            'github_dorking', 'secretfinder', 'linkfinder', 'arjun',
            'jsluice'  # jsluice has a binary but also has Python module
        ]

        installed = {}
        for tool in tools:
            if tool in python_modules:
                installed[tool] = self._check_python_module(tool)
            else:
                installed[tool] = check_tool_installed(tool)

        counts = sum(installed.values())
        log_success(f"{counts}/{len(tools)} tools already installed")
        return installed

    def _check_python_module(self, module_name: str) -> bool:
        """Check if a Python module is importable"""
        try:
            __import__(module_name.replace('-', '_'))
            return True
        except ImportError:
            return False

    def install_missing_tools(self, tools: List[str], installed: Dict[str, bool]):
        """Install tools that are missing"""
        to_install = [t for t in tools if not installed.get(t, False)]

        if not to_install:
            log_success("All required tools are already installed!")
            return True

        log_info(f"Installing {len(to_install)} missing tools...")

        # Add Go to PATH if needed
        ensure_git_binaries()

        for tool in to_install:
            if tool not in self.TOOL_INSTALL_MAP.get(self.os_family, {}):
                log_warning(f"No install method for {tool} on {self.os_family}. Skipping.")
                continue

            install_info = self.TOOL_INSTALL_MAP[self.os_family][tool]

            # Skip tools with None method (API-only or manually installed)
            if install_info is None:
                log_info(f"Skipping {tool}: no automatic install (API-only or manual setup required)")
                continue

            method, package = install_info

            if method == 'apt':
                success = install_via_apt(tool, package)
            elif method == 'go':
                success = install_via_go(tool, package)
            elif method == 'brew':
                success = install_via_brew(tool, package)
            elif method == 'pip':
                success = install_via_pip(tool, package)
            elif method == 'cargo':
                success = install_via_cargo(tool, package)
            else:
                log_warning(f"Unknown install method {method} for {tool}")
                success = False

            if success:
                self.installed_tools[tool] = True
            else:
                log_error(f"Failed to install {tool}")

        return True  # Continue even if some fail

    def install_via_brew(self, tool: str, package: str) -> bool:
        """Install via Homebrew (macOS)"""
        log_info(f"Installing {tool} via brew...")
        success, output = run_cmd(['brew', 'install', package], capture_output=True)
        if success:
            log_success(f"{tool} installed")
            return True
        else:
            log_error(f"Failed: {output}")
            return False

    def _check_docker_root_warning(self):
        """Check if running in Docker as non-root and warn about apt/pip issues"""
        # Detect Docker environment
        is_docker = os.path.exists('/.dockerenv') or os.path.exists('/.containerenv')
        if not is_docker:
            return

        # Check if running as root
        is_root = os.geteuid() == 0 if hasattr(os, 'geteuid') else False
        if is_root:
            return

        # Check if there are any apt or pip tools to install
        tools = self.PROFILE_TOOLS.get('professional', self.PROFILE_TOOLS['professional'])
        needs_apt = any(
            self.TOOL_INSTALL_MAP.get(self.os_family, {}).get(t) and
            self.TOOL_INSTALL_MAP[self.os_family][t] and
            self.TOOL_INSTALL_MAP[self.os_family][t][0] in ('apt', 'pip')
            for t in tools
            if t in self.TOOL_INSTALL_MAP.get(self.os_family, {})
        )

        if needs_apt:
            log_warning("")
            log_warning("=" * 60)
            log_warning("DETECTED: Running in Docker container as non-root user")
            log_warning("")
            log_warning("Some tools require root privileges for installation (apt/pip).")
            log_warning("")
            log_warning("RECOMMENDED: Run installer as root:")
            log_warning("  docker exec -u root huntforge-kali ./scripts/installer.py")
            log_warning("")
            log_warning("Alternative: Use virtualenv for Python tools only")
            log_warning("=" * 60)
            log_warning("")

    def setup_directories(self):
        """Create necessary directory structure"""
        self.huntforge_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.wordlists_dir.mkdir(parents=True, exist_ok=True)
        log_success(f"Created directories in {self.home}/.huntforge/")

    def create_config_files(self):
        """Create initial configuration files"""
        # Scope config
        scope_file = self.huntforge_dir / 'scope.json'
        if not scope_file.exists():
            default_scope = {
                "programs": {
                    "My Programs": {
                        "in_scope": ["example.com", "*.example.com"],
                        "out_of_scope": []
                    }
                }
            }
            with open(scope_file, 'w') as f:
                json.dump(default_scope, f, indent=2)
            log_success(f"Created {scope_file} (edit with your targets)")

        # Tool config (optional customization)
        tool_config = self.config_dir / 'tool_configs.yaml'
        if not tool_config.exists():
            # Copy from project if exists
            project_config = Path('config/tool_configs.yaml')
            if project_config.exists():
                shutil.copy(project_config, tool_config)
                log_success(f"Copied tool config to {tool_config}")

        log_success("Configuration files created")

    def download_minimal_wordlists(self):
        """Download minimal wordlists for testing (~100MB)"""
        if not self.download_wordlists:
            log_info("Skipping wordlist download (use --download-wordlists to enable)")
            return

        log_info("Downloading minimal wordlists...")
        wordlists = {
            'subdomains-top1million-110000.txt': 'https://github.com/danielmiessler/SecLists/raw/master/Discovery/DNS/subdomains-top1million-110000.txt',
            'raft-medium-directories.txt': 'https://github.com/danielmiessler/SecLists/raw/master/Discovery/Web-Content/raft-medium-directories.txt',
            'burp-parameter-names.txt': 'https://github.com/danielmiessler/SecLists/raw/master/Discovery/Web-Content/burp-parameter-names.txt',
        }

        for filename, url in wordlists.items():
            dest = self.wordlists_dir / filename
            if dest.exists():
                log_info(f"Wordlist already exists: {filename}")
                continue

            log_info(f"Downloading {filename}...")
            success, output = run_cmd(['curl', '-sL', url, '-o', str(dest)])
            if success and dest.exists():
                size = dest.stat().st_size / 1024 / 1024
                log_success(f"Downloaded {filename} ({size:.1f} MB)")
            else:
                log_warning(f"Failed to download {filename}")

    def verify_huntforge_imports(self) -> bool:
        """Verify HuntForge core modules can be imported"""
        try:
            # Add project root to path
            sys.path.insert(0, str(Path.cwd()))
            from core.tag_manager import TagManager
            from core.orchestrator_v2 import OrchestratorV2
            log_success("HuntForge core modules OK")
            return True
        except ImportError as e:
            log_warning(f"HuntForge imports failed in this environment: {e}")
            log_info("This is expected if not using the virtual environment (./venv/bin/python3)")
            return True # Don't fail the whole installation for this
        except Exception as e:
            log_error(f"HuntForge imports failed: {e}")
            return False

    def print_summary(self):
        """Print installation summary"""
        print("\n" + "="*60)
        print(f"{BOLD}Installation Complete!{RESET}")
        print("="*60)
        print(f"\nProfile: {self.profile}")
        print(f"Tools installed: {sum(self.installed_tools.values())}")

        print("\nNext steps:")
        print("  1. Edit ~/.huntforge/scope.json to add your targets")
        print("  2. Optional: Set API keys in .env file")
        print("  3. Run your first scan:")
        print(f"     python3 huntforge.py scan your-target.com")
        print("\nFor help: python3 huntforge.py --help")
        print("="*60 + "\n")

    def run(self) -> bool:
        """Run full installation"""
        print(f"{BOLD}HuntForge Installer{RESET}\n")

        if not self.check_prerequisites():
            return False

        # Warn about Docker non-root usage
        self._check_docker_root_warning()

        self.setup_directories()

        # Get tools for profile
        tools = self.PROFILE_TOOLS.get('professional', self.PROFILE_TOOLS['professional'])
        installed = self.check_existing_tools(tools)

        self.install_missing_tools(tools, installed)
        self.create_config_files()
        self.download_minimal_wordlists()
        self.verify_huntforge_imports()
        self.print_summary()

        return True


def main():
    parser = argparse.ArgumentParser(description="HuntForge Installer")
    parser.add_argument('--profile', choices=['professional'],
                       default='professional', help='Installation profile (default: professional)')
    parser.add_argument('--download-wordlists', action='store_true',
                       help='Download minimal wordlists (~100MB)')
    args = parser.parse_args()

    installer = HuntForgeInstaller(
        profile=args.profile,
        download_wordlists=args.download_wordlists
    )

    success = installer.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
