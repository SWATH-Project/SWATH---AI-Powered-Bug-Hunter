import os
import requests
import json
from modules.base_module import BaseModule

class CrtshModule(BaseModule):
    def build_command(self, target: str, container_out: str) -> list:
        # crt.sh doesn't need a container/binary, we can hit the API directly from python
        return []

    def run(self, target: str, output_dir: str, tag_manager, config: dict = None) -> dict:
        self.config = config or {}
        
        host_out = os.path.join(output_dir, 'raw', 'crtsh.json')
        os.makedirs(os.path.dirname(host_out), exist_ok=True)
        
        try:
            r = requests.get(f"https://crt.sh/?q=%.{target}&output=json", timeout=60)
            data = r.json()
            subs = set()
            for entry in data:
                name = entry.get('name_value', '')
                for n in name.split('\n'):
                    subs.add(n.strip().lower())
            results = list(subs)
            
            with open(host_out, 'w') as f:
                json.dump(results, f)
                
        except Exception:
            results = []
            
        return {'results': results, 'count': len(results), 'requests_made': 1}

    def emit_tags(self, result: dict, tag_manager) -> None:
        if result['count'] > 0:
            tag_manager.add('has_subdomains', confidence='high', source='crtsh')
