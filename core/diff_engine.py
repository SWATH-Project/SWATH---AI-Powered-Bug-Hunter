from loguru import logger
from core.database import Database

class DiffEngine:
    """
    Attack surface diffing engine comparing new scan results against historical database.
    """
    
    def __init__(self, db: Database):
        self.db = db

    def diff_assets(self, target_id: int, current_assets: list, scan_id: int) -> dict:
        """
        Compare current assets against what's in the DB.
        Returns a dict of new/removed/changed assets and records changes.
        current_assets = [{'type': 'subdomain', 'value': 'api.example.com', 'source': 'subfinder'}]
        """
        if not target_id:
            return {}
            
        # Get historical assets
        assets_list = self.db.get_assets(target_id)
        hist_assets = {(row['type'], row['value']): dict(row) for row in assets_list}
        
        diff = {'new': [], 'resurrected': [], 'removed': []}
        seen_current = set()
        
        # Check current against historical
        for asset in current_assets:
            key = (asset['type'], asset['value'])
            seen_current.add(key)
            
            if key not in hist_assets:
                # Brand new asset
                asset_id = self.db.upsert_asset(target_id, asset['type'], asset['value'], asset.get('source'))
                diff['new'].append(asset)
                self._record_change(target_id, asset_id, scan_id, 'new', 'asset', None, asset['value'])
                
            elif hist_assets[key]['is_alive'] == 0:
                # Asset was dead, now alive again
                asset_id = hist_assets[key]['id']
                self.db.upsert_asset(target_id, asset['type'], asset['value'], asset.get('source'))
                diff['resurrected'].append(asset)
                self._record_change(target_id, asset_id, scan_id, 'changed', 'is_alive', '0', '1')

        # Check historical against current to find dead assets
        for key, hist in hist_assets.items():
            if key not in seen_current and hist['is_alive'] == 1:
                # Asset is now dead/missing
                asset_id = hist['id']
                
                # Mark as dead in DB
                conn = self.db._get_conn()
                conn.execute('UPDATE assets SET is_alive = 0, last_seen = CURRENT_TIMESTAMP WHERE id = ?', (asset_id,))
                conn.commit()
                conn.close()
                
                diff['removed'].append(hist)
                self._record_change(target_id, asset_id, scan_id, 'changed', 'is_alive', '1', '0')

        return diff
        
    def _record_change(self, target_id, asset_id, scan_id, change_type, field, old_val, new_val):
        conn = self.db._get_conn()
        try:
            conn.execute('''
                INSERT INTO asset_changes (target_id, asset_id, scan_id, change_type, field_changed, old_value, new_value)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (target_id, asset_id, scan_id, change_type, field, old_val, new_val))
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to record change: {e}")
        finally:
            conn.close()

    def generate_diff_report(self, target_id: int, scan_id: int) -> str:
        conn = self.db._get_conn()
        cur = conn.execute('''
            SELECT change_type, field_changed, old_value, new_value, a.type as asset_type, a.value as asset_value
            FROM asset_changes ac
            JOIN assets a ON ac.asset_id = a.id
            WHERE ac.target_id = ? AND ac.scan_id = ?
        ''', (target_id, scan_id))
        changes = cur.fetchall()
        conn.close()
        
        if not changes:
            return "No changes detected since last scan."
            
        report = [f"Attack Surface Diff for Scan ID {scan_id}:"]
        for c in changes:
            report.append(f"- [{c['change_type'].upper()}] {c['asset_type']}: {c['asset_value']}")
        return "\n".join(report)
