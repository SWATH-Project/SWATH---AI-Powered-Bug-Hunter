import os
import json
from modules.base_module import BaseModule
from core.exceptions import EmptyOutputError, OutputParseError

WAF_TECH_NAMES = {
    'cloudflare': 'medium',
    'cloudflare-waf': 'high',
    'akamai-waf': 'high',
    'akamai-kona-site-defender': 'high',
    'akamai-web-application-protector': 'high',
    'imperva': 'medium',
    'imperva-securesphere': 'high',
    'incapsula': 'high',
    'sucuri-waf': 'high',
    'sucuri': 'medium',
    'aws-waf': 'high',
    'aws-waf-shield': 'high',
    'modsecurity': 'high',
    'modsecurity-crs': 'high',
    'f5-big-ip-asm': 'high',
    'f5-big-ip': 'medium',
    'fortiweb': 'high',
    'barracuda-waf': 'high',
    'wordfence': 'high',
}

CDN_TECH_NAMES = {
    'cloudflare',
    'cloudfront',
    'akamai',
    'fastly',
    'sucuri',
    'imperva',
    'incapsula',
    'azure-cdn',
    'stackpath',
}

WAF_WEBSERVER_NAMES = {
    'cloudflare',
    'incapsula',
    'imperva',
    'sucuri',
}

WAF_HEADER_INDICATORS = {
    'server': ['cloudflare', 'incapsula', 'imperva'],
    'x-iinfo': [],
    'x-cdn': [],
    'x-protected-by': [],
    'x-waf-event-info': [],
    'x-sucuri-id': [],
    'cf-ray': [],
}

CDN_HEADER_INDICATORS = {
    'via': ['cloudfront', 'akamai', 'fastly'],
    'x-cache': ['cloudfront'],
    'x-amz-cf-id': [],
    'x-fastly-request-id': [],
    'x-akamai-transformed': [],
    'cf-ray': [],
    'x-sucuri-id': [],
}


class HttpxModule(BaseModule):
    def build_command(self, target: str, output_file: str) -> list:
        cmd = [
            'httpx',
            '-json',
            '-silent',
            '-follow-redirects',
            '-status-code',
            '-tech-detect',
            '-title',
            '-content-length',
            '-o', output_file,
        ]
        return cmd

    def run(self, target: str, output_dir: str, tag_manager, config: dict = None, **kwargs) -> dict:
        self.config = config or {}

        host_input_file = os.path.join(output_dir, 'processed', 'all_subdomains.txt')
        if not os.path.exists(host_input_file):
            host_input_file = os.path.join(output_dir, 'raw', 'subfinder.txt')

        container_input_file = self._to_container_path(host_input_file)

        if not os.path.exists(host_input_file):
            return {'results': [], 'count': 0, 'requests_made': 0}

        host_output_file = os.path.join(output_dir, 'raw', 'httpx.json')
        container_output_file = self._to_container_path(host_output_file)

        os.makedirs(os.path.dirname(host_output_file), exist_ok=True)

        command = self.build_command(target, container_output_file)
        command += ['-l', container_input_file]

        if self._cfg('ports'):
            command += ['-p', self._cfg('ports')]

        self._run_subprocess(command, output_file=host_output_file)

        try:
            content = self._read_output_file(host_output_file)
            results = []
            for line in content.splitlines():
                if line.strip():
                    results.append(json.loads(line))
        except EmptyOutputError:
            results = []

        return {
            'results':       results,
            'count':         len(results),
            'requests_made': self.estimated_requests()
        }

    def emit_tags(self, result: dict, tag_manager) -> None:
        if result['count'] == 0:
            return

        urls = [r.get('url') for r in result['results'] if r.get('url')]
        tag_manager.add('has_live_web', confidence='high', evidence=urls[:5], source='httpx')

        waf_evidence = set()
        cdn_evidence = set()

        for r in result['results']:
            tech = r.get('tech', [])
            webserver = str(r.get('webserver', '')).lower()
            headers = r.get('headers', {})

            for t in tech:
                t_low = str(t).lower().strip()

                if 'wordpress' in t_low:
                    tag_manager.add('has_wordpress', confidence='high', source='httpx')

                if t_low in WAF_TECH_NAMES:
                    waf_evidence.add(t_low)
                elif t_low.startswith('waf'):
                    waf_evidence.add(t_low)

                if t_low in CDN_TECH_NAMES:
                    cdn_evidence.add(t_low)

            if webserver in WAF_WEBSERVER_NAMES:
                waf_evidence.add(f'webserver:{webserver}')

            if webserver in CDN_TECH_NAMES:
                cdn_evidence.add(f'webserver:{webserver}')

            for header_name, indicators in WAF_HEADER_INDICATORS.items():
                header_val = str(headers.get(header_name, '')).lower()
                if not header_val:
                    continue
                if not indicators:
                    if header_val:
                        waf_evidence.add(f'header:{header_name.lower()}')
                else:
                    for indicator in indicators:
                        if indicator in header_val:
                            waf_evidence.add(f'header:{header_name.lower()}')

            for header_name, indicators in CDN_HEADER_INDICATORS.items():
                header_val = str(headers.get(header_name, '')).lower()
                if not header_val:
                    continue
                if not indicators:
                    if header_val:
                        cdn_evidence.add(f'header:{header_name.lower()}')
                else:
                    for indicator in indicators:
                        if indicator in header_val:
                            cdn_evidence.add(f'header:{header_name.lower()}')

        if waf_evidence:
            has_high = any(
                ev in WAF_TECH_NAMES and WAF_TECH_NAMES[ev] == 'high'
                for ev in waf_evidence if ev in WAF_TECH_NAMES
            )
            confidence = 'high' if has_high else 'medium'
            tag_manager.add('has_waf', confidence=confidence, source='httpx', evidence=sorted(waf_evidence))

        if cdn_evidence:
            tag_manager.add('has_cdn', confidence='medium', source='httpx', evidence=sorted(cdn_evidence))

    def estimated_requests(self) -> int:
        return 500
