import glob
import os
import json
from modules.base_module import BaseModule
from core.exceptions import EmptyOutputError


class GauModule(BaseModule):
    def build_command(self, target: str = '', output_file: str = '', domains=None) -> list:
        cmd = ['gau']
        if domains:
            cmd.extend(domains)
        elif target:
            cmd.append(target)

        providers = self._cfg('providers')
        if providers:
            cmd.extend(['--providers', providers])
        threads = self._cfg('threads', 5)
        cmd.extend(['--threads', str(threads)])
        blacklist = self._cfg('blacklist')
        if blacklist:
            cmd.extend(['--blacklist', blacklist])
        return cmd

    def run(self, target: str, output_dir: str, tag_manager, config: dict = None,
            live_hosts: str = None, live_hosts_txt: str = None, **kwargs) -> dict:
        self.config = config or {}

        host_out = os.path.join(output_dir, 'raw', 'gau.txt')
        container_out = self._to_container_path(host_out)

        os.makedirs(os.path.dirname(host_out), exist_ok=True)

        input_file = None
        if live_hosts_txt and os.path.exists(live_hosts_txt):
            input_file = live_hosts_txt
        elif live_hosts and os.path.exists(live_hosts):
            try:
                with open(live_hosts) as f:
                    urls = json.load(f)
                if urls:
                    input_file = os.path.join(output_dir, 'raw', 'gau_input.txt')
                    with open(input_file, 'w') as f:
                        f.write('\n'.join(urls) + '\n')
            except (json.JSONDecodeError, Exception):
                pass

        for old in glob.glob(os.path.join(output_dir, 'raw', 'gau_*.txt')):
            try:
                os.remove(old)
            except OSError:
                pass

        if input_file:
            with open(input_file) as f:
                domains = [line.strip() for line in f if line.strip()]

            cmd = self.build_command(output_file=container_out, domains=domains)
            stdout = self._run_subprocess(cmd, output_file=host_out)
            if stdout:
                with open(host_out, 'w', encoding='utf-8') as f:
                    f.write(stdout)
            try:
                urls = [l for l in self._read_output_file(host_out).splitlines() if l.strip()]
            except EmptyOutputError:
                urls = []
        else:
            cmd = self.build_command(target=target, output_file=container_out)
            stdout = self._run_subprocess(cmd, output_file=host_out)
            if stdout:
                with open(host_out, 'w', encoding='utf-8') as f:
                    f.write(stdout)
            try:
                urls = [l for l in self._read_output_file(host_out).splitlines() if l.strip()]
            except EmptyOutputError:
                urls = []

        return {'results': urls, 'count': len(urls), 'requests_made': 50}

    def emit_tags(self, result: dict, tag_manager) -> None:
        if result['count'] > 0:
            tag_manager.add('params_found', confidence='low', source='gau')
