import os
import json
from modules.base_module import BaseModule
from core.exceptions import EmptyOutputError


class NucleiModule(BaseModule):
    """
    Vulnerability scanning via ProjectDiscovery Nuclei.
    Supports both standard and precision-targeted execution modes.
    """

    def build_command(self, target: str, container_out: str) -> list:
        cmd = ['nuclei', '-u', target, '-json-export', container_out, '-silent']

        severity = self._cfg('severity')
        if severity:
            cmd += ['-severity', severity]

        tags = self._cfg('tags')
        if tags:
            cmd += ['-tags', tags]

        templates = self._cfg('templates')
        if templates:
            cmd += ['-t', templates]
        else:
            # Use automatic scan when no templates specified
            cmd += ['-automatic-scan']

        return cmd

    def run(self, target: str, output_dir: str, tag_manager, config: dict = None, **kwargs) -> dict:
        self.config = config or {}

        host_output_file = os.path.join(output_dir, 'raw', 'nuclei.json')
        container_output_file = self._to_container_path(host_output_file)
        os.makedirs(os.path.dirname(host_output_file), exist_ok=True)

        # Resolve input: precision override > live hosts > default
        if 'all_urls' in kwargs and os.path.exists(kwargs['all_urls']):
            host_input_file = kwargs['all_urls']
        elif 'live_hosts_txt' in kwargs and os.path.exists(kwargs['live_hosts_txt']):
            host_input_file = kwargs['live_hosts_txt']
        else:
            host_input_file = os.path.join(output_dir, 'raw', 'httpx.json')

        container_input_file = self._to_container_path(host_input_file)

        # Build command based on input availability
        if os.path.exists(host_input_file) and not self._cfg('force_domain_only'):
            command = [
                'nuclei', '-l', container_input_file,
                '-json-export', container_output_file, '-silent'
            ]
            severity = self._cfg('severity')
            if severity:
                command += ['-severity', severity]
            tags = self._cfg('tags')
            if tags:
                command += ['-tags', tags]
            templates = self._cfg('templates')
            if templates:
                command += ['-t', templates]
            else:
                command += ['-automatic-scan']
        else:
            command = self.build_command(target, container_output_file)

        self._run_subprocess(command, output_file=host_output_file)

        results = self._parse_nuclei_output(host_output_file)

        return {
            'results':       results,
            'count':         len(results),
            'requests_made': self.estimated_requests()
        }

    def _parse_nuclei_output(self, filepath: str) -> list:
        """
        Resilient parser that handles both:
        - JSON array format (from -json-export): [{"template-id": ...}, ...]
        - NDJSON format (from -jsonl or stdout): {"template-id": ...}\n{"template-id": ...}
        """
        if not os.path.exists(filepath) or os.path.getsize(filepath) < 3:
            return []

        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                raw = f.read().strip()
        except OSError:
            return []

        if not raw:
            return []

        # Attempt 1: JSON array (from -json-export)
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict)]
            if isinstance(data, dict):
                return [data]
        except json.JSONDecodeError:
            pass

        # Attempt 2: NDJSON (one JSON object per line)
        results = []
        for line in raw.splitlines():
            line = line.strip()
            if line:
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        results.append(obj)
                except json.JSONDecodeError:
                    continue
        return results

    def emit_tags(self, result: dict, tag_manager) -> None:
        if result['count'] > 0:
            tag_manager.add('has_vulnerabilities', confidence='high', source='nuclei')

            severities = [
                r.get('info', {}).get('severity')
                for r in result['results']
                if isinstance(r, dict)
            ]
            if any(s in ['critical', 'high'] for s in severities):
                tag_manager.add('has_critical_vulns', confidence='high', source='nuclei')

    def estimated_requests(self) -> int:
        return 15000
