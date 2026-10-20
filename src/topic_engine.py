"""
src/topic_engine.py
===================
Topic rotation and status tracking, backed by the SQLite database (src/db.py).
"""

import logging
from typing import Optional, Dict, Any, List

from .db import PipelineDatabase

log = logging.getLogger(__name__)


class TopicEngine:
    def __init__(self, db: Optional[PipelineDatabase] = None):
        self.db = db or PipelineDatabase()

    def load_bank(self) -> List[Dict[str, Any]]:
        return self.db.list_all_topics()

    def get_published_ids(self) -> set:
        return {t["id"] for t in self.db.list_all_topics() if t["times_published"] > 0}

    def get_next_topic(self) -> Dict[str, Any]:
        return self.db.get_next_topic()

    def get_topic_by_id(self, topic_id: str) -> Optional[Dict[str, Any]]:
        return self.db.get_topic_by_id(topic_id)

    def record_status(self, topic_id: str, status: str, run_id: Optional[str] = None):
        self.db.record_topic_status(topic_id, status, run_id=run_id)
        log.info("Topic '%s' -> '%s'.", topic_id, status)
