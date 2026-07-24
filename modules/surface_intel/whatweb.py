import os
import json
from modules.base_module import BaseModule
from core.exceptions import EmptyOutputError, OutputParseError

class WhatWebModule(BaseModule):
    def build_command(self, target: str, container_out: str) -> list:
        return ['whatweb', target, '--log-json', container_out]

    def run(self, target: str, output_dir: str, tag_manager, config: dict = None, **kwargs) -> dict:
        self.config = config or {}

        host_output_file = os.path.join(output_dir, 'raw', 'whatweb.json')
        container_output_file = self._to_container_path(host_output_file)
        os.makedirs(os.path.dirname(host_output_file), exist_ok=True)

        # Check if we have httpx.json (JSON lines) from previous phase
        httpx_json_path = os.path.join(output_dir, 'raw', 'httpx.json')
        if os.path.exists(httpx_json_path):
            # Parse httpx JSON output to extract URLs
            urls = []
            try:
                with open(httpx_json_path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                data = json.loads(line)
                                url = data.get('url')
                                if url:
                                    urls.append(url)
                            except json.JSONDecodeError:
                                continue
            except Exception:
                urls = []

            if urls:
                # Limit to top 20 hosts to avoid timeout on large scopes
                max_hosts = int(self._cfg('max_hosts', 20))
                if len(urls) > max_hosts:
                    urls = urls[:max_hosts]

                # Write URLs to a plain text file for whatweb input
                urls_txt_path = os.path.join(output_dir, 'raw', 'httpx_urls.txt')
                with open(urls_txt_path, 'w') as f:
                    f.write('\n'.join(urls) + '\n')
                container_input = self._to_container_path(urls_txt_path)
                command = ['whatweb', '--input-file', container_input, '--log-json', container_output_file, '-q']
            else:
                # No URLs extracted, fallback to scanning the domain directly
                command = self.build_command(target, container_output_file)
        else:
            # No httpx.json, scan the domain directly
            command = self.build_command(target, container_output_file)

        self._run_subprocess(command, output_file=host_output_file)

        try:
            content = self._read_output_file(host_output_file)
            # whatweb --log-json writes JSON lines, not a JSON array
            results = []
            for line in content.splitlines():
                line = line.strip()
                if line:
                    try:
                        results.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            if not results:
                # Try parsing as a JSON array (older whatweb versions)
                try:
                    results = json.loads(content) if content.strip() else []
                except json.JSONDecodeError:
                    results = []
        except EmptyOutputError:
            results = []

        return {
            'results':       results,
            'count':         len(results),
            'requests_made': self.estimated_requests()
        }

    def emit_tags(self, result: dict, tag_manager) -> None:
        if result['count'] > 0:
            tag_manager.add('has_tech_intel', confidence='high', source='whatweb')

    def estimated_requests(self) -> int:
        return 50
