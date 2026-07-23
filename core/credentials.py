import os
import yaml
from loguru import logger

class CredentialManager:
    """
    Credential and session manager for SWATH tools.
    Note: Currently stores credentials in plaintext YAML.
    """
    
    def __init__(self, config_path="~/.swath/credentials.yaml"):
        self.config_path = os.path.expanduser(config_path)
        self.credentials = self._load_creds()

    def _load_creds(self):
        if not os.path.exists(self.config_path):
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                yaml.dump({}, f)
            return {}
        try:
            with open(self.config_path, 'r') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Failed to load credentials: {e}")
            return {}

    def _save_creds(self):
        try:
            with open(self.config_path, 'w') as f:
                yaml.dump(self.credentials, f)
        except Exception as e:
            logger.error(f"Failed to save credentials: {e}")

    def add_credential(self, domain: str, cred_type: str, value: str, label: str = "default"):
        """
        Add a credential (cookie, bearer_token, api_key, basic_auth, header).
        """
        if domain not in self.credentials:
            self.credentials[domain] = {}
            
        self.credentials[domain][label] = {
            'type': cred_type,
            'value': value
        }
        self._save_creds()
        logger.info(f"Added {cred_type} credential for {domain}")

    def get_credentials(self, domain: str) -> dict:
        # Check direct domain match
        if domain in self.credentials:
            return self.credentials[domain]
            
        # Check wildcard match (e.g. *.example.com applies to api.example.com)
        parts = domain.split('.')
        if len(parts) >= 2:
            wildcard = "*." + ".".join(parts[-2:])
            if wildcard in self.credentials:
                return self.credentials[wildcard]
                
        return {}

    def get_auth_headers(self, domain: str, label: str = "default") -> dict:
        creds = self.get_credentials(domain)
        if not creds or label not in creds:
            return {}
            
        cred = creds[label]
        headers = {}
        
        if cred['type'] == 'cookie':
            headers['Cookie'] = cred['value']
        elif cred['type'] == 'bearer_token':
            headers['Authorization'] = f"Bearer {cred['value']}"
        elif cred['type'] == 'basic_auth':
            import base64
            b64 = base64.b64encode(cred['value'].encode()).decode()
            headers['Authorization'] = f"Basic {b64}"
        elif cred['type'] == 'header':
            # expects value like "X-Api-Key: 12345"
            parts = cred['value'].split(':', 1)
            if len(parts) == 2:
                headers[parts[0].strip()] = parts[1].strip()
                
        return headers
