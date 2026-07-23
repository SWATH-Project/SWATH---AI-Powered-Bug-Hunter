# ai/report_generator.py
# Author         : SWATH Agent
# Responsibility : Generates the final bug bounty report using OpenRouter API.
#                  Produces a Mandiant-grade, client-deliverable security assessment.
# ------------------------------------------------------------

import os
import json
from datetime import datetime
from loguru import logger

from ai.openrouter_helper import OpenRouterHelper


SYSTEM_PROMPT = """\
You are SWATH AI — a senior offensive security analyst at a world-class threat intelligence firm.

Produce a **comprehensive, client-deliverable** reconnaissance and vulnerability assessment report in Markdown format.

## Required Report Sections

### 1. Executive Summary
- 2-3 paragraphs summarizing the engagement scope, key findings, and overall risk posture
- Include a risk rating: CRITICAL / HIGH / MEDIUM / LOW / INFORMATIONAL

### 2. Scope & Methodology
- Target domain and scan scope
- Tools employed and phases executed
- Timeframe and approach

### 3. Critical Findings
Present in table format:
| # | Finding | Severity | CVSS | Evidence | Impact |
Include ALL confirmed findings. If none found, explicitly state that.

### 4. Attack Surface Analysis
- Exposed subdomains and live hosts count
- Open ports and services detected
- Technology stack identified (frameworks, servers, CMS)
- API endpoints and parameterized URLs discovered

### 5. Intelligence Tags Summary
- List all discovered intelligence tags with their confidence levels
- Explain what each tag means for the security posture

### 6. Risk Matrix
Rate the overall target across these dimensions:
- External Attack Surface: LOW/MEDIUM/HIGH/CRITICAL
- Information Exposure: LOW/MEDIUM/HIGH/CRITICAL
- Vulnerability Density: LOW/MEDIUM/HIGH/CRITICAL
- WAF/Defense Maturity: LOW/MEDIUM/HIGH

### 7. Recommended Next Steps
Provide **specific, actionable** recommendations ordered by priority:
1. Immediate actions (0-24 hours)
2. Short-term fixes (1-7 days)
3. Medium-term improvements (1-4 weeks)
4. Long-term strategic recommendations

### 8. Appendix
- Raw data file inventory
- Tool execution summary

## Rules
- Be concise, factual, and actionable
- Do NOT fabricate findings — only report what the provided tags and intelligence confirm
- Use professional language suitable for C-level stakeholders
- Include severity ratings using industry-standard CVSS where applicable
- If a finding has low confidence, clearly mark it as "Requires Manual Verification"
"""


class ReportGenerator:
    def __init__(self, model: str = None):
        self.helper = OpenRouterHelper(model=model)

    def generate(self, domain: str, tag_manager, output_dir: str) -> str:
        """
        Generate an executive AI report from scan tags using OpenRouter.

        Args:
            domain:      Target domain that was scanned.
            tag_manager: TagManager instance with discovered intelligence.
            output_dir:  Path to the domain's output directory.

        Returns:
            The generated report text, or empty string on failure.
        """
        # Check if OpenRouter is reachable (API key is set)
        if not self.helper.is_available():
            logger.error(
                "OpenRouter API key is not set. Make sure to set OPENROUTER_API_KEY. "
                "Skipping AI report generation."
            )
            return ""

        logger.info(f"Generating final AI report for {domain} via OpenRouter ({self.helper.model})...")

        # ── Build context from tags ──────────────────────────────
        tags = tag_manager.get_all()

        context = f"Target Domain: {domain}\n"
        context += f"Report Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\n"

        # Scan metadata
        metadata_path = os.path.join(output_dir, 'scan_metadata.json')
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, 'r') as f:
                    metadata = json.load(f)
                duration_hrs = metadata.get('total_duration_seconds', 0) / 3600
                context += f"Scan Duration: {duration_hrs:.2f} hours\n"
                context += f"Tools Completed: {metadata.get('tools_completed', 'N/A')}\n"
                context += f"Tools Failed: {metadata.get('tools_failed', 'N/A')}\n\n"
            except Exception:
                pass

        context += "Discovered Intelligence Tags:\n"
        for tag_name, data in tags.items():
            confidence = data.get('confidence', 'unknown')
            source = data.get('source', 'unknown')
            context += f"  - {tag_name}  (confidence: {confidence}, source: {source})\n"

        # ── Add raw file stats if available ──────────────────────
        processed_dir = os.path.join(output_dir, 'processed')
        if os.path.isdir(processed_dir):
            context += "\nProcessed Data Files:\n"
            for fname in os.listdir(processed_dir):
                fpath = os.path.join(processed_dir, fname)
                if os.path.isfile(fpath):
                    size_kb = os.path.getsize(fpath) / 1024
                    context += f"  - {fname} ({size_kb:.1f} KB)\n"

        # ── Count discovered assets ──────────────────────────────
        subdomain_file = os.path.join(processed_dir, 'subdomains_merged.txt')
        if os.path.exists(subdomain_file):
            try:
                with open(subdomain_file) as f:
                    subdomain_count = sum(1 for line in f if line.strip())
                context += f"\nSubdomains Discovered: {subdomain_count}\n"
            except Exception:
                pass

        all_urls_file = os.path.join(processed_dir, 'all_urls.txt')
        if os.path.exists(all_urls_file):
            try:
                with open(all_urls_file) as f:
                    url_count = sum(1 for line in f if line.strip())
                context += f"URLs Discovered: {url_count}\n"
            except Exception:
                pass

        # ── Sample key raw output files for richer context ────────
        raw_dir = os.path.join(output_dir, 'raw')
        key_files = ['nuclei.json', 'dalfox.txt', 'subjack.txt', 'ffuf.json',
                     'httpx.json', 'whatweb.json', 'naabu.txt']
        if os.path.isdir(raw_dir):
            for fname in key_files:
                fpath = os.path.join(raw_dir, fname)
                if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                    try:
                        with open(fpath, 'r', encoding='utf-8', errors='ignore') as rf:
                            lines = rf.readlines()[:30]
                        sample = ''.join(lines)[:2000]  # Cap at 2000 chars
                        context += f"\n--- Sample from {fname} ---\n{sample}\n"
                    except Exception:
                        pass

        # ── Call OpenRouter ──────────────────────────────────────────
        prompt = (
            f"Generate a professional, client-deliverable security reconnaissance report "
            f"for the target '{domain}' based on the following intelligence:\n\n"
            f"{context}"
        )

        try:
            report_text = self.helper.generate(prompt=prompt, system=SYSTEM_PROMPT)

            # Ensure the logs directory exists
            logs_dir = os.path.join(output_dir, 'logs')
            os.makedirs(logs_dir, exist_ok=True)
            report_path = os.path.join(logs_dir, 'ai_report.md')

            # Write the report with professional header
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(f"# SWATH Security Assessment — {domain}\n\n")
                f.write(f"> **Classification:** CONFIDENTIAL — Client Deliverable\n")
                f.write(f"> **Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n")
                f.write(f"> **Engine:** SWATH AI ({self.helper.model})\n\n")
                f.write("---\n\n")
                f.write(report_text)

            # Also write a JSON summary for the dashboard
            summary_path = os.path.join(logs_dir, 'ai_report_meta.json')
            with open(summary_path, 'w') as f:
                json.dump({
                    'domain': domain,
                    'generated_at': datetime.utcnow().isoformat() + 'Z',
                    'model': self.helper.model,
                    'report_path': report_path,
                    'tags_count': len(tags),
                    'report_length': len(report_text)
                }, f, indent=2)

            logger.success(f"AI Report written to {report_path}")
            return report_text

        except Exception as e:
            logger.error(f"Failed to generate report via OpenRouter: {e}")
            return ""


# CLI wrapper
def generate_report(domain, tag_manager, output_dir):
    bot = ReportGenerator()
    return bot.generate(domain, tag_manager, output_dir)
