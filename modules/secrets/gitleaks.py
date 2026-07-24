import os
import json
from loguru import logger
from modules.base_module import BaseModule
from core.exceptions import EmptyOutputError

class GitleaksModule(BaseModule):
    def build_command(self, target: str, container_out: str) -> list:
        # The Docker volume mounts the project at /huntforge
        # So scan outputs live at /huntforge/output/<domain>
        source_dir = f"/huntforge/output/{target}"
        return ['gitleaks', 'detect', '--source', source_dir, '--report-path', container_out, '--no-git', '-v']

    def run(self, target: str, output_dir: str, tag_manager, config: dict = None) -> dict:
        self.config = config or {}
        
        host_out = os.path.join(output_dir, 'raw', 'gitleaks.json')
        container_out = self._to_container_path(host_out)
        os.makedirs(os.path.dirname(host_out), exist_ok=True)
        
        # gitleaks exit code 1 = leaks present, 0 = no leaks, other = fatal error
        try:
            self._run_subprocess(self.build_command(target, container_out))
        except Exception as e:
            if 'exited with code 1' not in str(e):
                logger.warning(f"Gitleaks execution error (might be expected if results found): {e}")

        leaks = []
        try:
            if os.path.exists(host_out) and os.path.getsize(host_out) > 0:
                content = self._read_output_file(host_out)
                leaks = json.loads(content)
                if not isinstance(leaks, list): leaks = []
        except (EmptyOutputError, json.JSONDecodeError):
            leaks = []
            
        return {'results': leaks, 'count': len(leaks), 'requests_made': 10}

    def emit_tags(self, result: dict, tag_manager) -> None:
        if result['count'] > 0:
            tag_manager.add('leaked_credentials', confidence='high', source='gitleaks')
