# core/tag_manager.py
# Author         : Member 1
# Responsibility : Store and retrieve tags set by tool modules.
#                  Acts as the shared knowledge board for the scan.
#                  Every tool reads from and writes to this.
# ------------------------------------------------------------

from datetime import datetime
import threading

# Confidence ranking — higher number = more confident
CONFIDENCE_RANK = {
    'low':    1,
    'medium': 2,
    'high':   3,
}


class TagManager:
    """
    Shared knowledge board for the entire scan. Thread-safe implementation.
    """

    def __init__(self):
        # Internal store — dict of tag_name → tag_data
        self._tags = {}
        self.lock = threading.Lock()

    @property
    def tags(self):
        with self.lock:
            return self._tags

    @tags.setter
    def tags(self, value):
        with self.lock:
            self._tags = value

    # ── Write ─────────────────────────────────────────────────────

    def add(self, tag: str, confidence: str = 'low',
            evidence: list = None, source: str = '') -> None:
        """
        Set a tag on the knowledge board securely via Lock.
        """
        with self.lock:
            # Validate confidence value
            if confidence not in CONFIDENCE_RANK:
                confidence = 'low'

            # If tag already exists — only upgrade, never downgrade
            if tag in self._tags:
                existing_rank = CONFIDENCE_RANK[self._tags[tag]['confidence']]
                new_rank      = CONFIDENCE_RANK[confidence]

                if new_rank <= existing_rank:
                    return

            # Set the tag
            self._tags[tag] = {
                'confidence': confidence,
                'evidence':   evidence or [],
                'source':     source,
                'set_at':     datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }

    # ── Read ──────────────────────────────────────────────────────

    def has(self, tag: str, min_confidence: str = 'low') -> bool:
        """
        Check if a tag exists with at least the given confidence securely.
        """
        with self.lock:
            if tag not in self._tags:
                return False

            existing_rank = CONFIDENCE_RANK[self._tags[tag]['confidence']]
            required_rank = CONFIDENCE_RANK.get(min_confidence, 1)

            return existing_rank >= required_rank

    def get(self, tag: str) -> dict:
        """
        Get the full data for a specific tag securely.
        """
        with self.lock:
            return self._tags.get(tag, None)

    def get_all(self) -> dict:
        """
        Get every tag currently set securely.
        """
        with self.lock:
            return dict(self._tags)

    def get_by_confidence(self, confidence: str) -> dict:
        """
        Get all tags at a specific confidence level securely.
        """
        with self.lock:
            return {
                tag: dict(data)
                for tag, data in self._tags.items()
                if data['confidence'] == confidence
            }

    # ── Utility ───────────────────────────────────────────────────

    def count(self) -> int:
        """Total number of tags currently set."""
        with self.lock:
            return len(self._tags)

    def save_to_file(self, output_dir: str) -> None:
        """
        Save all tags to active_tags.json safely.
        """
        import json
        import os

        filepath = os.path.join(output_dir, 'processed', 'active_tags.json')
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with self.lock:
            safe_copy = dict(self._tags)

        with open(filepath, 'w') as f:
            json.dump(safe_copy, f, indent=2)

    def __repr__(self) -> str:
        """Print-friendly summary of all tags."""
        with self.lock:
            if not self._tags:
                return "TagManager: no tags set"
            lines = ["TagManager:"]
            for tag, data in self._tags.items():
                lines.append(
                    f"  {tag:<35} "
                    f"{data['confidence']:<8} "
                    f"(source: {data['source']})"
                )
            return '\n'.join(lines)