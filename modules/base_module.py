# core/base_module.py
# Author      : Member 1
# Responsibility : Define the interface every tool module must follow.
#                 Handle all subprocess-level errors in one place.
#                 Member 2 inherits this — never rewrites subprocess logic.
#                 Executes all commands via DockerRunner.
# ------------------------------------------------------------

import os
import subprocess
import shutil
from pathlib import Path
from core.exceptions import (
    BinaryNotFoundError,
    EmptyOutputError,
    ToolExecutionError,
    ToolTimeoutError,
    DockerNotRunningError
)
from core.docker_runner import DockerRunner

# ── Ensure Go / tool binary directories are always in PATH ──────────
# When running inside the Docker container (especially as root),
# Go binaries installed to /go/bin may not be on PATH.
# We fix this once at import time so every shutil.which() call works.
_EXTRA_BIN_PATHS = [
    '/go/bin',
    '/usr/local/go/bin',
    os.path.expanduser('~/go/bin'),
    '/home/swath/go/bin',
    '/home/swath/.local/bin',
    os.path.expanduser('~/.local/bin'),
]

_current_path = os.environ.get('PATH', '')
for _p in _EXTRA_BIN_PATHS:
    if os.path.isdir(_p) and _p not in _current_path:
        os.environ['PATH'] = _p + os.pathsep + os.environ.get('PATH', '')
        _current_path = os.environ['PATH']


class BaseModule:
    """
    Parent class for every tool module in SWATH.

    Member 2 creates child classes like this:

        from modules.base_module import BaseModule

        class Subfinder(BaseModule):
            def build_command(self, target, output_file): ...
            def run(self, target, output_dir, tag_manager, config): ...
            def emit_tags(self, result, tag_manager): ...
            def estimated_requests(self): ...

    The orchestrator calls ONLY these 4 methods on every module:
        module.run()
        module.emit_tags()
        module.estimated_requests()
        module.build_command()  (called internally by run())
    """

    def __init__(self, docker_runner: DockerRunner = None):
        """
        Orchestrator injects a shared DockerRunner.
        If running standalone for testing, instantiate a new one.

        Auto-detects if running inside the Docker container: if the 'docker'
        binary is not found in PATH, we assume we're inside the container and
        will run commands directly via subprocess (no docker exec needed).
        """
        # Detect execution context: if docker CLI is available, we're on host and should use DockerRunner.
        # If docker CLI is NOT available, we're inside the container and run directly.
        self._docker_cli_available = shutil.which('docker') is not None

        if self._docker_cli_available:
            # Use provided docker_runner or create a new one
            self.docker_runner = docker_runner or DockerRunner()
        else:
            # Inside container - run commands directly, ignore docker_runner
            self.docker_runner = None

    # ── 4 Methods Member 2 Must Implement ────────────────────────

    def build_command(self, target: str, output_file: str) -> list:
        """
        Returns the exact shell command to run as a list of strings.

        Member 2 MUST override this. No safe default exists.

        Example return value:
            ['subfinder', '-d', 'example.com',
             '-o', '/output/example.com/raw/subfinder.txt',
             '-silent']
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement build_command()."
        )

    def run(self, target: str, output_dir: str,
            tag_manager, config: dict = None) -> dict:
        """
        Execute the tool and return results in standard format.

        Member 2 MUST override this. No safe default exists.

        Parameters:
            target      : domain being scanned e.g. 'example.com'
            output_dir  : base output path e.g. 'output/example.com'
            tag_manager : TagManager instance
            config      : dict from YAML config block — optional switches

        Must return:
            {
                'results':       list,
                'count':         int,
                'requests_made': int
            }
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement run()."
        )

    def emit_tags(self, result: dict, tag_manager) -> None:
        """
        Read the result dict and set tags on tag_manager.

        Member 2 SHOULD override this for most tools.
        Default: does nothing. Safe to leave as-is if tool
        doesn't produce findings that other phases depend on.
        """
        pass

    def estimated_requests(self) -> int:
        """
        Return a rough estimate of HTTP requests this tool will make.
        Used by BudgetTracker in Gate 3 before deciding to run the tool.

        Member 2 SHOULD override this. Default: 100.
        """
        return 100

    def extract_tags(self, result: dict) -> dict:
        """
        Extract tags from tool result. Called by orchestrator after run().

        Default: returns empty dict. Override in subclass if the tool
        needs to set tags via return value instead of emit_tags().
        """
        return {}

    # ── Internal Core Methods ─────────────────────────────────────

    def docker_command(self, target: str, output_file: str) -> list:
        """
        Wraps build_command() with the necessary docker exec prefix.
        Used if a module legitimately needs to see the raw docker command.
        Not available when running inside the container (docker CLI not present).
        """
        if self.docker_runner is None:
            raise RuntimeError(
                "docker_command() is not available when running inside the container. "
                "Use build_command() to get the raw command without docker prefix."
            )
        return ["docker", "exec", self.docker_runner.container_name] + self.build_command(target, output_file)

    def _run_subprocess(self, command: list, output_file: str = None) -> str:
        """
        Execute a shell command with smart timeout.
        
        Smart timeout behavior:
        - If tool produces output by the timeout → extend and let it finish
        - If tool produces NO output by the timeout → kill it
        
        Depending on execution context:
        - If running on host (docker CLI available): exec via DockerRunner into container.
        - If running inside container: run command directly via subprocess.
        
        Exceptions (ToolTimeoutError, ToolExecutionError, DockerNotRunningError)
        bubble up to the orchestrator.
        """
        from core.smart_timeout_v2 import SmartTimeoutV2
        
        tool_binary = command[0]
        timeout_seconds = self._cfg('timeout', default=300)

        # WAF Evasion Injection
        if hasattr(self, 'tag_manager') and self.tag_manager and self.tag_manager.has('has_waf', min_confidence='high'):
            import random
            user_agents = [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15",
                "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0"
            ]
            ua = random.choice(user_agents)
            tool_name = tool_binary.split('/')[-1]
            
            if tool_name == 'nuclei':
                if '-rl' not in command: command += ['-rl', '5']
                if '-H' not in command: command += ['-H', f'User-Agent: {ua}']
            elif tool_name == 'katana':
                if '-rl' not in command: command += ['-rl', '5']
                if '-H' not in command: command += ['-H', f'User-Agent: {ua}']
            elif tool_name == 'ffuf':
                if '-rate' not in command: command += ['-rate', '5']
                if '-H' not in command: command += ['-H', f'User-Agent: {ua}']
            elif tool_name == 'dalfox':
                if '--worker' not in command: command += ['--worker', '5']
                if '--delay' not in command: command += ['--delay', '200']
                if '-H' not in command: command += ['-H', f'User-Agent: {ua}']
            elif tool_name == 'wpscan':
                if '--throttle' not in command: command += ['--throttle', '200']
                if '--user-agent' not in command: command += ['--user-agent', ua]
            elif tool_name == 'sqlmap':
                if '--delay' not in command: command += ['--delay', '0.2']
                if '--random-agent' not in command: command += ['--random-agent']
            elif tool_name == 'whatweb':
                if '--max-threads' not in command: command += ['--max-threads', '2']
                if '--header' not in command: command += ['--header', f'User-Agent: {ua}']
            elif tool_name == 'httpx':
                if '-rl' not in command: command += ['-rl', '10']
                if '-H' not in command: command += ['-H', f'User-Agent: {ua}']

        if self.docker_runner is not None:
            # ── Docker mode: host running orchestrator, use docker exec ─────
            # Step 1: Check binary is installed INSIDE container
            try:
                if tool_binary.startswith('/'):
                    check_cmd = ['test', '-x', tool_binary]
                else:
                    check_cmd = ['which', tool_binary]
                check = self.docker_runner.exec_raw(check_cmd, timeout=5)
                if check.returncode != 0:
                    raise BinaryNotFoundError(
                        f"'{tool_binary}' is not installed inside the Kali container.",
                        tool=tool_binary
                    )
            except Exception as e:
                if isinstance(e, BinaryNotFoundError):
                    raise
                self.docker_runner.is_container_running()

            # Step 2: Run the tool with smart timeout via docker exec
            docker_cmd = ['docker', 'exec', self.docker_runner.container_name] + command
            
            runner = SmartTimeoutV2(
                command=docker_cmd,
                timeout=timeout_seconds,
                output_file=output_file,
                tool_name=tool_binary,
                is_docker=True,
            )
            return runner.run()
        else:
            # ── Direct mode: already inside container, run locally ─────
            resolved = self._find_binary(tool_binary)
            if not resolved:
                raise BinaryNotFoundError(
                    f"'{tool_binary}' is not installed or not in PATH.",
                    tool=tool_binary
                )
            # If we found it at an absolute path, rewrite command[0]
            if resolved != tool_binary:
                command[0] = resolved

            runner = SmartTimeoutV2(
                command=command,
                timeout=timeout_seconds,
                output_file=output_file,
                tool_name=tool_binary,
                is_docker=False,
            )
            return runner.run()

    def _read_output_file(self, host_filepath: str) -> str:
        """
        Read the output file a tool wrote to disk (from the Host perspective).

        Matches original behavior, reading the mapped `./output/...` file.
        Raises EmptyOutputError if file is missing or empty.
        """
        if not os.path.exists(host_filepath):
            raise EmptyOutputError(
                f"Output file was not created: {host_filepath}. "
                f"Tool may have failed silently.",
                tool=self.__class__.__name__
            )

        with open(host_filepath, 'r', encoding='utf-8') as f:
            content = f.read().strip()

        if not content:
            raise EmptyOutputError(
                f"Output file exists but is empty: {host_filepath}.",
                tool=self.__class__.__name__
            )

        return content

    def _cfg(self, key: str, default=None):
        """
        Safely read a value from self.config dict.
        Supports dot-notation for nested keys, e.g. _cfg('extra_args.threads').
        """
        if not hasattr(self, 'config') or self.config is None:
            return default
        if '.' not in key:
            return self.config.get(key, default)
        keys = key.split('.')
        value = self.config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    @staticmethod
    def _find_binary(name: str) -> str:
        """
        Locate a tool binary by name.
        Returns the resolved path string, or '' if not found.
        """
        # 1. Already an absolute path that exists?
        if os.path.isabs(name) and os.path.isfile(name) and os.access(name, os.X_OK):
            return name

        # 2. Standard PATH lookup
        found = shutil.which(name)
        if found:
            return found

        # 3. Fallback: check common installation directories
        for directory in _EXTRA_BIN_PATHS:
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate

        return ''

    def _to_container_path(self, path: str) -> str:
        """
        Convert a filesystem path to a format usable inside the Linux container.
        """
        if not path:
            return ""
            
        # Get the absolute path of the project root
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        abs_path = os.path.abspath(path)
        
        # If the path is within the project root, map it to /swath
        if abs_path.startswith(project_root):
            rel_path = os.path.relpath(abs_path, project_root)
            container_path = f"/swath/{rel_path}"
        else:
            container_path = abs_path
            
        return os.path.normpath(container_path).replace('\\', '/')
