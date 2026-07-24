import os
import json
import tempfile
from modules.base_module import BaseModule
from core.exceptions import EmptyOutputError, OutputParseError, BinaryNotFoundError

class WappalyzerModule(BaseModule):
    def build_command(self, target: str, container_out: str) -> list:
        return ['wappalyzer_cli', f'https://{target}', '--output', 'json']

    def run(self, target: str, output_dir: str, tag_manager, config: dict = None, **kwargs) -> dict:
        self.config = config or {}

        host_output_file = os.path.join(output_dir, 'raw', 'wappalyzer.json')
        container_output_file = self._to_container_path(host_output_file)
        os.makedirs(os.path.dirname(host_output_file), exist_ok=True)

        httpx_json_path = os.path.join(output_dir, 'raw', 'httpx.json')
        urls = []
        if os.path.exists(httpx_json_path):
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

        try:
            if urls:
                max_hosts = int(self._cfg('max_hosts', 20))
                if len(urls) > max_hosts:
                    urls = urls[:max_hosts]

                urls_txt_path = os.path.join(output_dir, 'raw', 'httpx_urls.txt')
                with open(urls_txt_path, 'w') as f:
                    f.write('\n'.join(urls) + '\n')

                all_results = []
                for url in urls:
                    cmd = ['wappalyzer_cli', url, '--output', 'json']
                    try:
                        self._run_subprocess(cmd, output_file=host_output_file)
                    except (BinaryNotFoundError, EmptyOutputError):
                        continue

                    try:
                        content = self._read_output_file(host_output_file)
                        try:
                            parsed = json.loads(content)
                            if isinstance(parsed, dict):
                                all_results.append(parsed)
                            elif isinstance(parsed, list):
                                all_results.extend(parsed)
                        except json.JSONDecodeError:
                            continue
                    except EmptyOutputError:
                        continue

                results = all_results
            else:
                command = self.build_command(target, container_output_file)
                self._run_subprocess(command, output_file=host_output_file)

                try:
                    content = self._read_output_file(host_output_file)
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict):
                            results = [parsed]
                        elif isinstance(parsed, list):
                            results = parsed
                        else:
                            results = []
                    except json.JSONDecodeError:
                        results = []
                except EmptyOutputError:
                    results = []
        except BinaryNotFoundError:
            results = []

        return {
            'results':       results,
            'count':         len(results),
            'requests_made': self.estimated_requests()
        }

    def emit_tags(self, result: dict, tag_manager) -> None:
        if result['count'] > 0:
            tag_manager.add('has_tech_intel', confidence='high', source='wappalyzer')

        waf_signatures = {'cloudflare', 'akamai', 'imperva', 'incapsula', 'sucuri'}

        for entry in result.get('results', []):
            technologies = entry.get('technologies', [])
            if isinstance(technologies, dict):
                technologies = list(technologies.keys())

            tech_names = set()
            for tech in technologies:
                if isinstance(tech, str):
                    tech_names.add(tech.lower())
                elif isinstance(tech, dict):
                    name = tech.get('name', '')
                    if name:
                        tech_names.add(name.lower())

            if 'wordpress' in tech_names:
                tag_manager.add('has_wordpress', confidence='high', source='wappalyzer')

            if tech_names & waf_signatures:
                tag_manager.add('has_waf', confidence='high', source='wappalyzer')

            for name in tech_names:
                if 'api' in name or 'graphql' in name or 'rest' in name:
                    tag_manager.add('has_api_subdomain', confidence='medium', source='wappalyzer')
                    break

    def estimated_requests(self) -> int:
        return 50
