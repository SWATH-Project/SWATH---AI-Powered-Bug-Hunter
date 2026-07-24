import os
from modules.base_module import BaseModule
from core.exceptions import EmptyOutputError


class NaabuModule(BaseModule):
    def build_command(self, target: str, container_out: str) -> list:
        return ['naabu', '-host', target, '-o', container_out, '-silent']

    def run(self, target: str, output_dir: str, tag_manager, config: dict = None, subdomains_file: str = None, **kwargs) -> dict:
        self.config = config or {}

        host_output_file = os.path.join(output_dir, 'raw', 'naabu.txt')
        container_output_file = self._to_container_path(host_output_file)
        os.makedirs(os.path.dirname(host_output_file), exist_ok=True)

        # If subdomains file is provided, use -list mode
        if subdomains_file and os.path.exists(subdomains_file):
            container_input = self._to_container_path(subdomains_file)
            cmd = ['naabu', '-list', container_input, '-o', container_output_file, '-silent']
        else:
            # Fallback: check processed/all_subdomains.txt
            subs_file = os.path.join(output_dir, 'processed', 'all_subdomains.txt')
            if os.path.exists(subs_file):
                container_input = self._to_container_path(subs_file)
                cmd = ['naabu', '-list', container_input, '-o', container_output_file, '-silent']
            else:
                cmd = self.build_command(target, container_output_file)

        if self._cfg('top_ports'):
            cmd += ['-top-ports', str(self._cfg('top_ports'))]

        self._run_subprocess(cmd, output_file=host_output_file)

        try:
            content = self._read_output_file(host_output_file)
            ports = [p.strip() for p in content.splitlines() if p.strip()]
        except EmptyOutputError:
            ports = []

        return {
            'results': ports,
            'count': len(ports),
            'requests_made': self.estimated_requests()
        }

    def emit_tags(self, result: dict, tag_manager) -> None:
        if result['count'] > 0:
            tag_manager.add('has_open_ports', confidence='high', evidence=result['results'][:5], source='naabu')

    def estimated_requests(self) -> int:
        return 1000
