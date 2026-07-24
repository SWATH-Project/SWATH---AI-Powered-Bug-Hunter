import os
import json
from modules.base_module import BaseModule
from core.exceptions import EmptyOutputError

class SubjackModule(BaseModule):
    def build_command(self, target: str, container_out: str) -> list:
        # subjack expects a file list of subdomains
        return ['subjack', '-o', container_out, '-a', '-m']

    def run(self, target: str, output_dir: str, tag_manager, config: dict = None) -> dict:
        self.config = config or {}
        
        host_input_file = os.path.join(output_dir, 'processed', 'all_subdomains.txt')
        if not os.path.exists(host_input_file):
            host_input_file = os.path.join(output_dir, 'raw', 'subfinder.txt')
            
        container_input_file = self._to_container_path(host_input_file)
        
        if not os.path.exists(host_input_file):
            return {'results': [], 'count': 0, 'requests_made': 0}
            
        host_output_file = os.path.join(output_dir, 'raw', 'subjack.txt')
        container_output_file = self._to_container_path(host_output_file)
        os.makedirs(os.path.dirname(host_output_file), exist_ok=True)

        command = self.build_command(target, container_output_file)
        command += ['-w', container_input_file]
        
        self._run_subprocess(command, output_file=host_output_file)

        try:
            content = self._read_output_file(host_output_file)
            all_lines = [line.strip() for line in content.splitlines() if line.strip()]
            # CRITICAL: Filter out "[Not Vulnerable]" entries — those are NOT findings
            vulns = [line for line in all_lines if '[Not Vulnerable]' not in line and line]
        except EmptyOutputError:
            vulns = []

        return {
            'results':       vulns,
            'count':         len(vulns),
            'requests_made': self.estimated_requests()
        }

    def emit_tags(self, result: dict, tag_manager) -> None:
        if result['count'] > 0:
            tag_manager.add('has_subdomain_takeover', confidence='high',
                            evidence=result['results'][:5], source='subjack')
            tag_manager.add('has_vulnerabilities', confidence='high', source='subjack')

    def estimated_requests(self) -> int:
        return 300
