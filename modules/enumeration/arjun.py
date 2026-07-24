import os
import json
from modules.base_module import BaseModule
from core.exceptions import EmptyOutputError

class ArjunModule(BaseModule):
    def build_command(self, target: str, container_out: str) -> list:
        return ['arjun', '-u', f'https://{target}', '-oJ', container_out, '-t', '10']

    def run(self, target: str, output_dir: str, tag_manager, config: dict = None) -> dict:
        self.config = config or {}
        
        host_out = os.path.join(output_dir, 'raw', 'arjun.json')
        container_out = self._to_container_path(host_out)
        os.makedirs(os.path.dirname(host_out), exist_ok=True)
        
        self._run_subprocess(self.build_command(target, container_out), output_file=host_out)
        
        try:
            data = json.loads(self._read_output_file(host_out))
            params = []
            for url, pl in data.items():
                params.extend(pl)
        except (EmptyOutputError, json.JSONDecodeError):
            params = []
            
        return {'results': params, 'count': len(params), 'requests_made': 5000}

    def emit_tags(self, result: dict, tag_manager) -> None:
        if result['count'] > 0:
            tag_manager.add('hidden_params_found', confidence='high', source='arjun')
