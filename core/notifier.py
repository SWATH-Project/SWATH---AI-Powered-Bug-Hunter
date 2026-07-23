import os
import yaml
import requests
import threading
from loguru import logger

class Notifier:
    """
    Notification hub for SWATH alerts to multiple channels.
    """
    
    def __init__(self, config_path="~/.swath/notifications.yaml"):
        self.config_path = os.path.expanduser(config_path)
        self.config = self._load_config()
        self.channels = self.config.get('channels', {})

    def _load_config(self):
        if not os.path.exists(self.config_path):
            self.create_default_config()
        try:
            with open(self.config_path, 'r') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Failed to load notifications config: {e}")
            return {}

    def create_default_config(self):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        default_config = {
            'channels': {
                'discord': {
                    'webhook': '',
                    'events': ['critical_found', 'high_found', 'scan_complete']
                },
                'slack': {
                    'webhook': '',
                    'events': ['scan_complete']
                }
            }
        }
        try:
            with open(self.config_path, 'w') as f:
                yaml.dump(default_config, f)
        except Exception:
            pass

    def notify(self, event_type: str, title: str, message: str, data: dict = None):
        """Send notification to all subscribed channels asynchronously."""
        def send_worker():
            for channel_name, channel_cfg in self.channels.items():
                subscribed_events = channel_cfg.get('events', [])
                if event_type in subscribed_events or '*' in subscribed_events:
                    try:
                        self._dispatch(channel_name, channel_cfg, title, message, data)
                    except Exception as e:
                        logger.error(f"Failed to send {channel_name} notification: {e}")
        
        thread = threading.Thread(target=send_worker, daemon=True)
        thread.start()

    def _dispatch(self, channel: str, cfg: dict, title: str, message: str, data: dict):
        webhook = cfg.get('webhook', '').strip()
        if not webhook:
            return
            
        if channel == 'discord':
            payload = {"content": f"**{title}**\n{message}"}
            requests.post(webhook, json=payload, timeout=5)
            
        elif channel == 'slack':
            payload = {"text": f"*{title}*\n{message}"}
            requests.post(webhook, json=payload, timeout=5)
            
        # Extensible for telegram, email, etc.

    def notify_finding(self, finding_dict: dict):
        severity = finding_dict.get('severity', 'info').lower()
        event_type = f"{severity}_found"
        title = f"[{severity.upper()}] {finding_dict.get('title')}"
        message = f"Target: {finding_dict.get('target')}\nTool: {finding_dict.get('tool')}"
        self.notify(event_type, title, message, finding_dict)

    def notify_scan_complete(self, scan_summary: dict):
        title = f"Scan Complete: {scan_summary.get('domain')}"
        message = f"Status: {scan_summary.get('status')}\nTags: {scan_summary.get('tags_count')}\nTime: {scan_summary.get('duration')}s"
        self.notify('scan_complete', title, message, scan_summary)
