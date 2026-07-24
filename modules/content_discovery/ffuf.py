import os
import json
from loguru import logger
from modules.base_module import BaseModule
from core.exceptions import EmptyOutputError


class FfufModule(BaseModule):
    """
    Content discovery via ffuf (Fuzz Faster U Fool).
    Automatically resolves the best available wordlist inside the container.
    """

    # Wordlist search order — checked top to bottom, first match wins.
    WORDLIST_SEARCH_PATHS = [
        '/usr/share/seclists/Discovery/Web-Content/raft-medium-words.txt',
        '/usr/share/seclists/Discovery/Web-Content/common.txt',
        '/usr/share/dirb/wordlists/common.txt',
        '/usr/share/dirbuster/wordlists/directory-list-2.3-medium.txt',
        '/usr/share/wordlists/dirb/common.txt',
    ]

    def _resolve_wordlist(self) -> str:
        """
        Resolve the wordlist path to an existing file.
        1. Try the configured path from methodology YAML
        2. Fall back to known system wordlist locations
        """
        wl_config = self._cfg('wordlist', '')
        if wl_config:
            wl = os.path.expanduser(wl_config)
            if os.path.exists(wl):
                return wl

        # If docker_runner is set, check inside the container
        if self.docker_runner is not None:
            for fallback in self.WORDLIST_SEARCH_PATHS:
                try:
                    result = self.docker_runner.exec_raw(['test', '-f', fallback], timeout=5)
                    if result.returncode == 0:
                        return fallback
                except Exception:
                    continue
        else:
            for fallback in self.WORDLIST_SEARCH_PATHS:
                if os.path.exists(fallback):
                    return fallback

        logger.warning(
            "No wordlist found on disk. ffuf will likely fail. "
            "Install seclists: apt install seclists"
        )
        return '/usr/share/dirb/wordlists/common.txt'

    def build_command(self, target: str, container_out: str) -> list:
        wl = self._resolve_wordlist()

        cmd = [
            'ffuf',
            '-u', f'https://{target}/FUZZ',
            '-w', wl,
            '-o', container_out,
            '-of', 'json',
            '-s',            # silent mode
            '-t', '40',      # threads
            '-timeout', '10',  # per-request timeout
        ]

        match_codes = self._cfg('match_codes')
        if match_codes:
            cmd.extend(['-mc', str(match_codes)])
        cmd.append('-ac')  # auto-calibrate

        return cmd

    def run(self, target: str, output_dir: str, tag_manager, config: dict = None, **kwargs) -> dict:
        self.config = config or {}
        self.tag_manager = tag_manager

        host_out = os.path.join(output_dir, 'raw', 'ffuf.json')
        container_out = self._to_container_path(host_out)
        os.makedirs(os.path.dirname(host_out), exist_ok=True)

        try:
            self._run_subprocess(self.build_command(target, container_out), output_file=host_out)
        except Exception as e:
            # If ffuf timed out but produced output, treat as partial success
            if os.path.exists(host_out) and os.path.getsize(host_out) > 10:
                logger.warning(f"ffuf terminated ({e}) but produced partial output — parsing results.")
            else:
                raise

        results = self._parse_ffuf_output(host_out)

        return {'results': results, 'count': len(results), 'requests_made': 5000}

    def _parse_ffuf_output(self, filepath: str) -> list:
        """
        Resilient JSON parser for ffuf output.
        Handles truncated/partial JSON from interrupted runs.
        """
        if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
            return []

        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                raw = f.read().strip()
        except OSError:
            return []

        if not raw:
            return []

        # Attempt 1: Standard JSON parse
        try:
            content = json.loads(raw)
            return content.get('results', [])
        except json.JSONDecodeError:
            pass

        # Attempt 2: Fix truncated JSON — if the file was cut mid-write,
        # try to salvage what we can by closing unclosed arrays/objects.
        try:
            # Try to find the results array and parse it
            idx = raw.find('"results"')
            if idx != -1:
                # Find the opening bracket
                bracket_start = raw.find('[', idx)
                if bracket_start != -1:
                    # Count brackets to find valid subset
                    depth = 0
                    last_valid = bracket_start
                    for i in range(bracket_start, len(raw)):
                        if raw[i] == '[':
                            depth += 1
                        elif raw[i] == ']':
                            depth -= 1
                            if depth == 0:
                                last_valid = i
                                break
                    # If we didn't find matching bracket, close it manually
                    if depth > 0:
                        subset = raw[bracket_start:last_valid + 1] + ']'
                    else:
                        subset = raw[bracket_start:last_valid + 1]

                    results = json.loads(subset)
                    if isinstance(results, list):
                        logger.info(f"ffuf: Salvaged {len(results)} results from truncated output")
                        return results
        except (json.JSONDecodeError, ValueError):
            pass

        logger.warning("ffuf: Could not parse output file at all")
        return []

    def emit_tags(self, result: dict, tag_manager) -> None:
        if result['count'] > 0:
            paths = [r.get('url', '') for r in result['results'] if isinstance(r, dict)]
            if any('admin' in p.lower() for p in paths):
                tag_manager.add('admin_panel_found', confidence='high', source='ffuf')
            if any('.bak' in p.lower() or '.zip' in p.lower() for p in paths):
                tag_manager.add('backup_files_found', confidence='high', source='ffuf')
            tag_manager.add('has_discovered_paths', confidence='high',
                            evidence=paths[:10], source='ffuf')

    def estimated_requests(self) -> int:
        return 5000
