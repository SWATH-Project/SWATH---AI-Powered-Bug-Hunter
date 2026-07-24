import os
import requests
import json
from modules.base_module import BaseModule

class GraphqlVoyagerModule(BaseModule):
    def build_command(self, target: str, container_out: str) -> list:
        return []

    def run(self, target: str, output_dir: str, tag_manager, config: dict = None) -> dict:
        self.config = config or {}
        
        host_out = os.path.join(output_dir, 'raw', 'graphql_schema.json')
        os.makedirs(os.path.dirname(host_out), exist_ok=True)
        
        query = """
        query IntrospectionQuery {
          __schema { queryType { name } }
        }
        """
        
        paths = ["/graphql", "/api/graphql", "/v1/graphql"]
        results = []
        
        for path in paths:
            try:
                r = requests.post(f"https://{target}{path}", json={"query": query}, timeout=10)
                if '__schema' in r.text:
                    results.append(path)
            except:
                pass
                
        with open(host_out, 'w') as f:
            json.dump(results, f)
            
        return {'results': results, 'count': len(results), 'requests_made': len(paths)}

    def emit_tags(self, result: dict, tag_manager) -> None:
        if result['count'] > 0:
            tag_manager.add('graphql_introspected', confidence='high', source='graphql_voyager')
