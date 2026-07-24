import os
import re
import json
from modules.base_module import BaseModule
from core.exceptions import EmptyOutputError


class SQLMapModule(BaseModule):
    def build_command(self, target: str, container_out: str) -> list:
        cmd = ['sqlmap', '--batch', '--random-agent', '--output', container_out]

        threads = self._cfg('threads', 4)
        level = self._cfg('level', 2)
        risk = self._cfg('risk', 2)

        cmd += ['--threads', str(threads), '--level', str(level), '--risk', str(risk)]

        if self._cfg('smart'):
            cmd += ['--smart']

        return cmd

    def run(self, target: str, output_dir: str, tag_manager, config: dict = None, **kwargs) -> dict:
        self.config = config or {}

        host_out = os.path.join(output_dir, 'raw', 'sqlmap_summary.txt')
        os.makedirs(os.path.dirname(host_out), exist_ok=True)

        sqlmap_output_dir = os.path.join(output_dir, 'raw', 'sqlmap_output')
        os.makedirs(sqlmap_output_dir, exist_ok=True)
        container_sqlmap_output = self._to_container_path(sqlmap_output_dir)

        if 'parameters' in kwargs and os.path.exists(kwargs['parameters']):
            params_file = kwargs['parameters']
        elif 'all_urls' in kwargs and os.path.exists(kwargs['all_urls']):
            params_file = kwargs['all_urls']
        else:
            params_file = os.path.join(output_dir, 'processed', 'parameters.json')
            
        container_params_file = None

        if os.path.exists(params_file):
            try:
                with open(params_file, 'r') as f:
                    params_data = json.load(f)
                urls = []
                if isinstance(params_data, list):
                    for param_entry in params_data:
                        if isinstance(param_entry, dict):
                            url = param_entry.get('url', '')
                            if url:
                                urls.append(url)
                        elif isinstance(param_entry, str):
                            urls.append(param_entry)
                if urls:
                    urls_file = os.path.join(output_dir, 'raw', 'sqlmap_urls.txt')
                    with open(urls_file, 'w') as f:
                        f.write('\n'.join(urls[:20]))
                    container_params_file = self._to_container_path(urls_file)
            except (json.JSONDecodeError, KeyError):
                pass

        base_cmd = self.build_command(target, container_sqlmap_output)

        if container_params_file:
            command = base_cmd + ['-m', container_params_file]
        else:
            command = base_cmd + ['-u', f'https://{target}']

        stdout = self._run_subprocess(command, output_file=host_out)

        results = self._parse_stdout(stdout)

        with open(host_out, 'w') as f:
            f.write(stdout or '')

        return {
            'results': results,
            'count': len(results),
            'requests_made': self.estimated_requests()
        }

    def _parse_stdout(self, stdout: str) -> list:
        results = []
        if not stdout:
            return results

        vulnerable_patterns = [
            r'(?i)(\S+)\s+is vulnerable',
            r'(?i)(\S+)\s+is injectable',
            r'(?i)parameter:\s+(\S+)\s+.*vulnerable',
            r'(?i)sqlmap identified the following injection',
        ]

        current_url = ''
        for line in stdout.splitlines():
            url_match = re.search(r'(?i)(?:target|url):\s*(\S+)', line)
            if url_match:
                current_url = url_match.group(1)

            for pattern in vulnerable_patterns:
                match = re.search(pattern, line)
                if match:
                    result_entry = {
                        'type': 'sql_injection',
                        'parameter': match.group(1) if match.lastindex else 'unknown',
                        'url': current_url,
                    }
                    results.append(result_entry)
                    break

        return results

    def emit_tags(self, result: dict, tag_manager) -> None:
        if result['count'] > 0:
            tag_manager.add('sqli_found', confidence='high', source='sqlmap')
            tag_manager.add('has_vulnerabilities', confidence='high', source='sqlmap')

    def estimated_requests(self) -> int:
        return 15000
