import os
import yaml
import random
from loguru import logger

class StealthManager:
    """
    Stealth profile system for evasive scanning.
    """
    
    def __init__(self, config_path="config/stealth_profiles.yaml"):
        self.config_path = os.path.abspath(config_path)
        self.profiles = self._load_profiles()
        self.current_profile = self.profiles.get('ninja', {})

    def _load_profiles(self):
        if not os.path.exists(self.config_path):
            self.create_default_config()
        try:
            with open(self.config_path, 'r') as f:
                data = yaml.safe_load(f)
                return data.get('profiles', {}) if data else {}
        except Exception as e:
            logger.error(f"Failed to load stealth profiles: {e}")
            return {}

    def create_default_config(self):
        parent_dir = os.path.dirname(self.config_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        default_config = {
            'profiles': {
                'ghost': {
                    'description': 'Maximum stealth — very slow but nearly undetectable',
                    'global_rate_limit': 2,
                    'delay_range': '500-2000ms',
                    'user_agent_rotation': True,
                    'proxy_chain': ['socks5://127.0.0.1:9050'],
                    'dns_resolver': '1.1.1.1',
                    'skip_tools': ['naabu', 'ffuf', 'dalfox']
                },
                'ninja': {
                    'description': 'Balanced — moderate speed, low detection risk',
                    'global_rate_limit': 10,
                    'delay_range': '100-500ms',
                    'user_agent_rotation': True,
                    'skip_tools': ['dalfox']
                },
                'blitz': {
                    'description': 'Maximum speed — for authorized pentests only',
                    'global_rate_limit': 0,
                    'delay_range': '0',
                    'user_agent_rotation': False,
                    'skip_tools': []
                }
            }
        }
        try:
            with open(self.config_path, 'w') as f:
                yaml.dump(default_config, f)
        except Exception:
            pass

    def set_profile(self, profile_name: str):
        if profile_name in self.profiles:
            self.current_profile = self.profiles[profile_name]
            logger.info(f"Stealth profile set to: {profile_name}")
            return True
        logger.warning(f"Stealth profile {profile_name} not found.")
        return False

    def get_profile(self, name: str = None) -> dict:
        if name and name in self.profiles:
            return self.profiles[name]
        return self.current_profile

    def apply_to_tool_config(self, tool_config: dict) -> dict:
        """Inject stealth parameters into a tool's configuration."""
        profile = self.current_profile
        
        # Override rate limit if profile specifies it
        if profile.get('global_rate_limit', 0) > 0:
            tool_config['rate_limit'] = profile['global_rate_limit']
            
        if profile.get('user_agent_rotation'):
            tool_config['user_agent'] = self.get_random_user_agent()
            
        return tool_config

    def should_skip_tool(self, tool_name: str) -> bool:
        return tool_name in self.current_profile.get('skip_tools', [])

    def get_random_user_agent(self):
        # A simple list for now
        uas = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) Firefox/91.0"
        ]
        return random.choice(uas)
