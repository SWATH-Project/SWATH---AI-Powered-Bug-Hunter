# ai/methodology_engine.py
# Author         : SWATH Agent
# Responsibility : Generates methodology using OpenRouter API.
#                  Reads default methodology first, then asks AI to
#                  create a focused version based on the user's prompt.
# ------------------------------------------------------------

import os
import yaml
import json
from loguru import logger

from ai.openrouter_helper import OpenRouterHelper

# ── Default methodology path ──────────────────────────────────

DEFAULT_METHOD_PATH = os.path.join('config', 'default_methodology.yaml')

# ── Tool Reference (used when default methodology can't be loaded) ──

TOOL_REFERENCE = """
SWATH has these tools available, organized by phase:

Phase 1 — Passive Recon: subfinder, crtsh
Phase 2 — Secrets & OSINT: gitleaks, trufflehog
Phase 3 — Live Asset Discovery: httpx, naabu
Phase 4 — Surface Intelligence: whatweb, wappalyzer_cli
Phase 5 — Enumeration: katana, gau, paramspider, arjun, graphql_voyager
Phase 6 — Content Discovery: ffuf, wpscan
Phase 7 — Vulnerability Scanning: nuclei, nuclei_auth, subjack, dalfox, sqlmap

Tool details:
- subfinder: Passive subdomain enumeration from multiple sources
- crtsh: Certificate transparency log lookup for subdomains
- gitleaks: Scan for hardcoded secrets/credentials in repos
- trufflehog: Scan for leaked credentials in GitHub orgs
- httpx: Probe hosts for HTTP services, detect tech stack
- naabu: Port scanner for open port discovery
- whatweb: Web technology fingerprinting
- wappalyzer_cli: Technology detection via Wappalyzer
- katana: Web crawler for URL/endpoints discovery
- gau: Gather URLs from Wayback/CommonCrawl
- paramspider: Discover URL parameters that may be injectable
- arjun: HTTP parameter discovery (find hidden params)
- graphql_voyager: GraphQL schema introspection
- ffuf: Fast web fuzzer for directory/path discovery
- wpscan: WordPress vulnerability scanner
- nuclei: Template-based vulnerability scanner
- nuclei_auth: Nuclei with auth/default-login templates
- subjack: Subdomain takeover detection
- dalfox: XSS scanner
- sqlmap: SQL injection detection and exploitation
"""

# ── System Prompt ──────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are SWATH AI, an expert bug bounty methodology generator.

YOUR TASK:
Given the user's specific reconnaissance goal and the DEFAULT METHODOLOGY below, 
generate a FOCUSED methodology that includes ONLY the phases and tools relevant 
to that particular goal. Remove any tool or phase that is irrelevant.

CRITICAL RULES:
1. Your output MUST be ONLY valid YAML. No markdown fences. No explanations.
2. You MUST preserve the exact phase key naming convention: phase_1_passive_recon, phase_2_secrets_osint, phase_3_asset_discovery, phase_4_surface_intelligence, phase_5_enumeration, phase_6_content_discovery, phase_7_vuln_scan
3. You MUST preserve the numerical phase ordering so execution happens in the correct order.
4. Each phase MUST have: label, description, and either "tools" or "conditional_tools"
5. The "tools" list uses this format for unconditional tools:
     tools:
       subfinder:
         enabled: true
         timeout: 180
         output_file: "raw/subfinder.txt"
6. The "conditional_tools" list uses this format for conditional tools:
     conditional_tools:
       - tool: arjun
         always: false
         if_tag: "has_api"
         timeout: 180
7. Include "tags_emitted" at the phase level (not per tool) — these drive conditional execution in later phases.
8. Include "input_files" and "output_files" for each phase when applicable.
9. You MAY skip entire phases if they are completely irrelevant to the goal, BUT you must keep phase order (don't renumber).
10. If you keep a phase, include ALL tools from that phase that are relevant — don't leave a phase with zero tools.
11. For vulnerability scanning phases, be SPECIFIC about which vulnerability types to scan for based on the goal.
12. ALWAYS include Phase 1 (passive recon) — subdomain discovery is foundational.
13. Include the "meta" block with version, name, description, and author fields.
14. Include "global_defaults" with timeout, retries, rate_limit, output_format, enabled.
15. Use ONLY these SWATH tool names: subfinder, crtsh, gitleaks, trufflehog, httpx, naabu, whatweb, wappalyzer_cli, katana, gau, paramspider, arjun, graphql_voyager, ffuf, wpscan, nuclei, nuclei_auth, subjack, dalfox, sqlmap.
16. DO NOT invent tool names that are not in the list above.
17. DO NOT include comments in the YAML output.
"""


class MethodologyEngine:
    def __init__(self, model: str = None):
        self.helper = OpenRouterHelper(model=model)

    def generate(self, prompt: str) -> dict:
        """
        Calls OpenRouter to generate a focused SWATH methodology YAML.
        
        Strategy:
        1. Load the default methodology as a reference
        2. Build a rich prompt that includes the default methodology
        3. Ask the AI to create a focused version based on the user's goal
        4. Fall back to default methodology on failure
        """
        logger.info(f"Asking OpenRouter ({self.helper.model}) to generate methodology for: {prompt}")

        # Check if OpenRouter is reachable (API key is set)
        if not self.helper.is_available():
            logger.error(
                "OpenRouter API key is not set. Make sure to set OPENROUTER_API_KEY. "
                "Falling back to default methodology."
            )
            return self._load_default()

        # Step 1: Load the default methodology as context
        default_methodology = self._load_default()
        default_yaml_str = self._methodology_to_compact_yaml(default_methodology)

        # Step 2: Build the prompt
        full_prompt = self._build_prompt(prompt, default_yaml_str)

        try:
            output_text = self.helper.generate(
                prompt=full_prompt,
                system=SYSTEM_PROMPT,
            )

            # Strip markdown fences if the LLM leaked them
            output_text = self._strip_markdown(output_text)

            methodology = yaml.safe_load(output_text)

            # Basic validation
            if not isinstance(methodology, dict) or 'phases' not in methodology:
                raise ValueError("Generated YAML missing 'phases' root key")

            # Validate tool names
            self._validate_tool_names(methodology)

            logger.success("Methodology generated successfully via OpenRouter.")
            return methodology

        except Exception as e:
            logger.error(f"Failed to generate methodology via OpenRouter: {e}")
            logger.warning("Falling back to default methodology.")
            return self._load_default()

    def _build_prompt(self, user_goal: str, default_yaml: str) -> str:
        """
        Build a rich prompt that includes the default methodology as reference.
        """
        prompt = f"""\
USER'S RECONNAISSANCE GOAL:
{user_goal}

DEFAULT METHODOLOGY (for reference — create a focused version based on the goal above):
{default_yaml}

INSTRUCTIONS:
Using the DEFAULT METHODOLOGY above as your template, create a FOCUSED methodology 
that is tailored to the user's specific reconnaissance goal.

- Keep ALL phases that are relevant to the goal, with their full structure (input_files, output_files, tags_emitted)
- Remove tools from each phase that are NOT relevant to the goal
- Remove entire phases that are completely irrelevant (but keep the phase key naming)
- If the goal is about a specific vulnerability type (e.g., XSS, SQLi), emphasize the relevant scanning tools
- If the goal is about a specific technology (e.g., WordPress, APIs), include the relevant detection tools
- Always keep Phase 1 (passive recon) as it's foundational
- Preserve all phase dependencies, tags, and conditional logic
- For conditional_tools, include the if_tag field so they only run when relevant

Generate ONLY the YAML output now:
"""
        return prompt

    def _methodology_to_compact_yaml(self, methodology: dict) -> str:
        """
        Convert the default methodology to a compact YAML string for the prompt.
        
        We strip out comments and overly verbose sections to keep the prompt
        within reasonable token limits, while preserving the structure.
        """
        compact = {}
        
        if 'meta' in methodology:
            compact['meta'] = methodology['meta']
        
        if 'global_defaults' in methodology:
            compact['global_defaults'] = methodology['global_defaults']
        
        compact['phases'] = {}
        phases = methodology.get('phases', {})
        
        for phase_key, phase_config in phases.items():
            compact_phase = {
                'label': phase_config.get('label', ''),
                'description': phase_config.get('description', ''),
            }
            
            if 'depends_on' in phase_config:
                compact_phase['depends_on'] = phase_config['depends_on']
            
            if 'input_files' in phase_config:
                compact_phase['input_files'] = phase_config['input_files']
            
            if 'output_files' in phase_config:
                compact_phase['output_files'] = phase_config['output_files']
            
            if 'tags_emitted' in phase_config:
                compact_phase['tags_emitted'] = phase_config['tags_emitted']
            
            if 'tools' in phase_config:
                compact_tools = {}
                for tool_name, tool_config in phase_config['tools'].items():
                    compact_tools[tool_name] = {
                        'enabled': tool_config.get('enabled', True),
                        'timeout': tool_config.get('timeout', 300),
                        'output_file': tool_config.get('output_file', ''),
                    }
                compact_phase['tools'] = compact_tools
            
            if 'conditional_tools' in phase_config:
                compact_cond_tools = []
                for tool_entry in phase_config['conditional_tools']:
                    compact_entry = {
                        'tool': tool_entry.get('tool'),
                        'always': tool_entry.get('always', True),
                    }
                    if 'if_tag' in tool_entry:
                        compact_entry['if_tag'] = tool_entry['if_tag']
                    if 'timeout' in tool_entry:
                        compact_entry['timeout'] = tool_entry['timeout']
                    if 'output_file' in tool_entry:
                        compact_entry['output_file'] = tool_entry['output_file']
                    compact_cond_tools.append(compact_entry)
                compact_phase['conditional_tools'] = compact_cond_tools
            
            compact['phases'][phase_key] = compact_phase
        
        return yaml.dump(compact, default_flow_style=False, sort_keys=False)

    def _validate_tool_names(self, methodology: dict):
        """
        Validate that all tool names in the generated methodology are valid.
        Log warnings for unknown tools but don't fail.
        """
        valid_tools = {
            'subfinder', 'crtsh', 'gitleaks', 'trufflehog',
            'httpx', 'naabu', 'whatweb', 'wappalyzer_cli',
            'katana', 'gau', 'paramspider', 'arjun', 'graphql_voyager',
            'ffuf', 'wpscan',
            'nuclei', 'nuclei_auth', 'subjack', 'dalfox', 'sqlmap',
        }
        
        phases = methodology.get('phases', {})
        for phase_key, phase_config in phases.items():
            tools = phase_config.get('tools', {})
            if isinstance(tools, dict):
                for tool_name in tools.keys():
                    if tool_name not in valid_tools:
                        logger.warning(f"Unknown tool '{tool_name}' in generated methodology (phase {phase_key})")
            
            cond_tools = phase_config.get('conditional_tools', [])
            if isinstance(cond_tools, list):
                for entry in cond_tools:
                    if isinstance(entry, dict):
                        tool_name = entry.get('tool', '')
                        if tool_name and tool_name not in valid_tools:
                            logger.warning(f"Unknown tool '{tool_name}' in generated methodology (phase {phase_key})")

    # ── Private helpers ──────────────────────────────────────────

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Remove ```yaml ... ``` fences that local models sometimes add."""
        if "```yaml" in text:
            text = text.split("```yaml", 1)[1].split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0]
        return text.strip()

    @staticmethod
    def _load_default() -> dict:
        with open(DEFAULT_METHOD_PATH, 'r') as f:
            return yaml.safe_load(f)


# Wrapper function for the CLI
def generate_methodology(prompt: str) -> dict:
    engine = MethodologyEngine()
    return engine.generate(prompt)
