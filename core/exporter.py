import os
import json
import csv
from loguru import logger

class Exporter:
    """
    Multi-format export engine for SWATH findings.
    """
    
    def export(self, findings: list, format_type: str, output_path: str, target_info: dict = None):
        format_type = format_type.lower()
        
        if format_type == 'markdown':
            self.export_markdown(findings, output_path)
        elif format_type == 'csv':
            self.export_csv(findings, output_path)
        elif format_type == 'json':
            self.export_json(findings, output_path)
        elif format_type == 'hackerone':
            self.export_hackerone(findings, output_path)
        else:
            logger.error(f"Unsupported export format: {format_type}")

    def export_markdown(self, findings: list, output_path: str):
        with open(output_path, 'w') as f:
            f.write("# SWATH Scan Report\n\n")
            for item in findings:
                f.write(f"## [{item.get('severity', 'INFO').upper()}] {item.get('title')}\n")
                f.write(f"**Type:** {item.get('type')}\n")
                f.write(f"**Asset:** {item.get('asset_value')}\n\n")
                f.write("### Description\n")
                f.write(f"{item.get('description', 'No description provided.')}\n\n")
                f.write("### Evidence\n")
                f.write(f"```\n{item.get('evidence', '')}\n```\n\n")
                f.write("---\n")
        logger.info(f"Exported markdown to {output_path}")

    def export_csv(self, findings: list, output_path: str):
        if not findings:
            return
            
        # Ensure findings are dicts
        dict_findings = [dict(f) for f in findings]
        keys = dict_findings[0].keys()
        with open(output_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(dict_findings)
        logger.info(f"Exported CSV to {output_path}")

    def export_json(self, findings: list, output_path: str):
        with open(output_path, 'w') as f:
            json.dump(findings, f, indent=4)
        logger.info(f"Exported JSON to {output_path}")

    def export_hackerone(self, findings: list, output_path: str):
        with open(output_path, 'w') as f:
            for item in findings:
                f.write(f"## Title: {item.get('title')}\n\n")
                f.write("### Summary\n")
                f.write(f"{item.get('description', '')}\n\n")
                f.write("### Steps To Reproduce\n")
                f.write("1. Send the following request:\n")
                f.write(f"```\n{item.get('evidence', '')}\n```\n\n")
                f.write("### Impact\n")
                f.write("Add impact details here based on vulnerability type.\n\n")
                f.write("---\n\n")
        logger.info(f"Exported HackerOne format to {output_path}")
