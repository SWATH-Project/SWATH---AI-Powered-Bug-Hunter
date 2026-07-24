# modules/passive/subfinder.py
# Author         : Member 2
# Responsibility : Run subfinder, parse subdomains, set tags.
# Inherits from  : BaseModule
# Raises         : OutputParseError (its own parsing error)
# Lets bubble    : BinaryNotFoundError, ToolTimeoutError,
#                  ToolExecutionError, DockerNotRunningError
# ------------------------------------------------------------

import os
from modules.base_module import BaseModule
from core.exceptions import EmptyOutputError, OutputParseError


class SubfinderModule(BaseModule):

    def build_command(self, target: str, container_output_file: str) -> list:
        """
        Builds the subfinder shell command to be run INSIDE the container.
        """
        cmd = ['subfinder', '-d', target, '-o', container_output_file, '-silent']

        if self._cfg('recursive', default=False):
            cmd += ['-recursive']

        cmd += ['-timeout', str(self._cfg('timeout', default=30))]

        max_results = self._cfg('max_results')
        if max_results:
            cmd += ['-max-results', str(max_results)]

        sources = self._cfg('sources')
        if sources:
            cmd += ['-sources', ','.join(sources)]

        cmd += ['-t', str(self._cfg('threads', default=10))]

        return cmd

    def run(self, target: str, output_dir: str, tag_manager, config: dict = None) -> dict:
        """
        Runs subfinder against target.
        """
        self.config = config or {}
        
        # ── Output Path Mapping ──────────────────────────────────
        # Windows Host Path: where the python script creates dirs and reads
        # Container Path: where the tool writes inside Docker
        host_output_file = os.path.join(output_dir, 'raw', 'subfinder.txt')
        container_output_file = self._to_container_path(host_output_file)
        
        os.makedirs(os.path.dirname(host_output_file), exist_ok=True)

        # ── Execution ──────────────────────────────────────────────
        command = self.build_command(target, container_output_file)
        self._run_subprocess(command, output_file=host_output_file)

        # ── Parsing ────────────────────────────────────────────────
        try:
            content = self._read_output_file(host_output_file)
            subdomains = self._parse(content)
        except EmptyOutputError:
            subdomains = []

        return {
            'results':       subdomains,
            'count':         len(subdomains),
            'requests_made': self.estimated_requests()
        }

    def emit_tags(self, result: dict, tag_manager) -> None:
        """Sets tags based on discovered subdomains."""
        if result['count'] == 0:
            return

        tag_manager.add(
            'has_subdomains',
            confidence='high',
            evidence=result['results'][:5],
            source='subfinder'
        )

        subdomains = result['results']
        if any('admin' in s for s in subdomains):
            tag_manager.add('has_admin_subdomain', confidence='medium', source='subfinder')
        if any('api' in s for s in subdomains):
            tag_manager.add('has_api_subdomain', confidence='medium', source='subfinder')
        if any('dev' in s or 'staging' in s for s in subdomains):
            tag_manager.add('has_dev_subdomain', confidence='medium', source='subfinder')
        if any('mail' in s for s in subdomains):
            tag_manager.add('has_mail_subdomain', confidence='low', source='subfinder')

    def estimated_requests(self) -> int:
        return 40

    def _parse(self, content: str) -> list:
        try:
            subdomains = [
                line.strip()
                for line in content.splitlines()
                if line.strip() and '.' in line
            ]
            return subdomains
        except Exception as e:
            raise OutputParseError(f"Could not parse subfinder output: {e}", tool='subfinder')