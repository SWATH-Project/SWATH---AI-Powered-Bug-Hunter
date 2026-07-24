import os
from modules.base_module import BaseModule
from core.exceptions import EmptyOutputError


class ParamspiderModule(BaseModule):
    def build_command(self, target: str, output_file: str) -> list:
        # paramspider is installed via pipx inside the container
        # Since docker exec doesn't source ~/.local/bin, we use the absolute path.
        cmd = ['/home/huntforge/.local/bin/paramspider', '-d', target, '-s']
        level = self.config.get('level')
        if level:
            cmd += ['--level', str(level)]
        return cmd

    def run(self, target: str, output_dir: str, tag_manager, config: dict = None, **kwargs) -> dict:
        self.config = config or {}

        host_out = os.path.join(output_dir, 'raw', 'paramspider.txt')
        container_out = self._to_container_path(host_out)
        os.makedirs(os.path.dirname(host_out), exist_ok=True)

        # The runner automatically captures stdout and writes it to host_out if provided.
        # We don't need to manually write it again.
        self._run_subprocess(self.build_command(target, container_out), output_file=host_out)

        # Fallback check: Some versions write to results/{domain}.txt by default
        default_results_dir = os.path.join(output_dir, "results")
        default_file = os.path.join(default_results_dir, f"{target}.txt")
        if not os.path.exists(host_out) or os.path.getsize(host_out) < 500: # Mostly just header
             if os.path.exists(default_file):
                 with open(default_file, 'r') as df, open(host_out, 'a') as hf:
                     hf.write(df.read())

        try:
            raw_lines = self._read_output_file(host_out).splitlines()
            # Filter for actual URLs (must contain http and no [INFO] tags or ASCII art chars like /_/)
            urls = []
            for line in raw_lines:
                line = line.strip()
                if line.startswith('http') and '://' in line and '[' not in line:
                    urls.append(line)
        except EmptyOutputError:
            urls = []

        return {'results': urls, 'count': len(urls), 'requests_made': 20}

    def emit_tags(self, result: dict, tag_manager) -> None:
        if result['count'] > 0:
            tag_manager.add('params_found', confidence='high', source='paramspider')
