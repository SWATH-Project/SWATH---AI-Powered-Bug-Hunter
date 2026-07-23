from loguru import logger
from core.database import Database

class BountyManager:
    """
    Bounty management and statistics tracker for SWATH.
    """
    
    def __init__(self, db: Database):
        self.db = db

    def mark_reported(self, finding_id: int, platform: str, report_url: str = None):
        conn = self.db._get_conn()
        try:
            conn.execute('''
                UPDATE findings 
                SET is_reported = 1, platform = ?, report_url = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (platform, report_url, finding_id))
            conn.commit()
            logger.info(f"Finding {finding_id} marked as reported on {platform}")
        except Exception as e:
            logger.error(f"Error marking finding as reported: {e}")
        finally:
            conn.close()

    def mark_resolved(self, finding_id: int, bounty_amount: float):
        conn = self.db._get_conn()
        try:
            conn.execute('''
                UPDATE findings 
                SET bounty_amount = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (bounty_amount, finding_id))
            conn.commit()
            logger.info(f"Finding {finding_id} marked as resolved with bounty ${bounty_amount}")
        except Exception as e:
            logger.error(f"Error marking finding as resolved: {e}")
        finally:
            conn.close()

    def mark_false_positive(self, finding_id: int):
        conn = self.db._get_conn()
        try:
            conn.execute('UPDATE findings SET is_false_positive = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?', (finding_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Error marking finding as FP: {e}")
        finally:
            conn.close()

    def get_stats(self) -> dict:
        conn = self.db._get_conn()
        stats = {
            'total_bounty': 0.0,
            'by_platform': {},
            'by_severity': {}
        }
        try:
            # Total Bounty
            cur = conn.execute('SELECT SUM(bounty_amount) as total FROM findings WHERE bounty_amount IS NOT NULL')
            row = cur.fetchone()
            if row and row['total']:
                stats['total_bounty'] = row['total']
                
            # By Platform
            cur = conn.execute('SELECT platform, SUM(bounty_amount) as total FROM findings WHERE bounty_amount IS NOT NULL GROUP BY platform')
            for row in cur.fetchall():
                if row['platform']:
                    stats['by_platform'][row['platform']] = row['total']
                    
            # Reported by severity
            cur = conn.execute('SELECT severity, COUNT(*) as count FROM findings WHERE is_reported = 1 GROUP BY severity')
            for row in cur.fetchall():
                stats['by_severity'][row['severity']] = row['count']
                
        except Exception as e:
            logger.error(f"Error getting bounty stats: {e}")
        finally:
            conn.close()
            
        return stats
