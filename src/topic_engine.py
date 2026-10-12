"""
src/topic_engine.py
===================
Manages topic rotation, state tracking, and history for the autonomous content pipeline.
Powered by atomic SQLite WAL persistence (src/db.py) to guarantee thread-safe execution,
zero write-collisions, and automatic JSON mirroring.
"""

from pathlib import Path
from typing import Optional, Dict, Any, List

from .db import PipelineDatabase


class TopicEngine:
    def __init__(self, db: Optional[PipelineDatabase] = None):
        self.db = db or PipelineDatabase()

    def load_bank(self) -> List[Dict[str, Any]]:
        topics = self.db.list_all_topics()
        # Enrich with full topic detail if needed
        return [self.db.get_topic_by_id(t["id"]) for t in topics]

    def get_published_ids(self) -> set:
        topics = self.db.list_all_topics()
        return {t["id"] for t in topics if t["status"] in ("published", "approved")}

    def get_next_topic(self) -> Dict[str, Any]:
        """
        Retrieves the next unproduced topic from SQLite database.
        """
        return self.db.get_next_topic()

    def get_topic_by_id(self, topic_id: str) -> Optional[Dict[str, Any]]:
        return self.db.get_topic_by_id(topic_id)

    def record_status(self, topic_id: str, status: str, details: Optional[Dict[str, Any]] = None):
        """
        Records the progression status of a topic atomically.
        """
        run_id = details.get("run_id") if details else None
        self.db.record_topic_status(topic_id, status, run_id=run_id)
        print(f"[TOPIC ENGINE] Topic '{topic_id}' recorded as '{status}' in SQLite WAL.")


if __name__ == "__main__":
    engine = TopicEngine()
    next_topic = engine.get_next_topic()
    print(f"Next topic: {next_topic['title']} ({next_topic['id']})")
