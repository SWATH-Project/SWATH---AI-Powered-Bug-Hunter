import os
import json
from loguru import logger
from modules.base_module import BaseModule
from core.exceptions import EmptyOutputError

class TrufflehogModule(BaseModule):
    def build_command(self, target: str, container_out: str) -> list:
        # Strip TLD to guess GitHub org name (e.g., example.com -> example)
        # Some targets might be just 'example' already.
        org_name = target.split('.')[0] if '.' in target else target
        return ['trufflehog', 'github', '--org', org_name, '--json']

    def run(self, target: str, output_dir: str, tag_manager, config: dict = None, **kwargs) -> dict:
        self.config = config or {}
        
        host_out = os.path.join(output_dir, 'raw', 'trufflehog.json')
        os.makedirs(os.path.dirname(host_out), exist_ok=True)
        
        # Trufflehog prints newline-delimited JSON to stdout.
        # BaseModule._run_subprocess will capture this.
        try:
            stdout = self._run_subprocess(self.build_command(target, host_out), output_file=host_out)
        except Exception as e:
            if 'exited with code 1' not in str(e):
                logger.warning(f"Trufflehog execution error: {e}")
            stdout = ""

        findings = []
        try:
            if os.path.exists(host_out) and os.path.getsize(host_out) > 0:
                # Trufflehog (Go version) outputs NDJSON
                with open(host_out, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                findings.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
        except Exception:
            findings = []
            
        return {'results': findings, 'count': len(findings), 'requests_made': 50}

    def emit_tags(self, result: dict, tag_manager) -> None:
        if result['count'] > 0:
            tag_manager.add('leaked_credentials', confidence='high', source='trufflehog')
