"""
src/topic_engine.py
===================
Topic rotation and status tracking, backed by the SQLite database (src/db.py).
"""

import logging
from typing import Optional, Dict, Any, List

from datetime import datetime, timedelta, timezone
import requests

from .db import PipelineDatabase

log = logging.getLogger(__name__)


def fetch_pageview_metrics(article_title: str, timeout: float = 8.0) -> Dict[str, Any]:
    """Fetches 30-day pageview traffic, daily average, and spike ratio from Wikimedia REST API (100% free, no key needed)."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=30)).strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")

    title_slug = article_title.replace(" ", "_")
    url = f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/all-agents/{title_slug}/daily/{start}/{end}"
    headers = {"User-Agent": "RelicAndRiddleStudioBot/1.0 (historical research; contact: studio@relicriddle.org)"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code != 200:
            return {"views_30d": 0, "daily_avg": 0.0, "spike_ratio": 1.0, "status": "unavailable"}
        items = r.json().get("items", [])
        if not items:
            return {"views_30d": 0, "daily_avg": 0.0, "spike_ratio": 1.0, "status": "empty"}
        views = [item["views"] for item in items]
        total_30d = sum(views)
        daily_avg = total_30d / len(views)
        recent_7d_avg = sum(views[-7:]) / max(1, len(views[-7:]))
        spike_ratio = round(recent_7d_avg / max(1.0, daily_avg), 2)
        return {
            "views_30d": total_30d,
            "daily_avg": round(daily_avg, 1),
            "spike_ratio": spike_ratio,
            "status": "success",
        }
    except Exception as exc:
        return {"views_30d": 0, "daily_avg": 0.0, "spike_ratio": 1.0, "error": str(exc), "status": "error"}


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

    def rank_topics_by_momentum(self, topics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Ranks candidate topics by organic search momentum score (views_30d * spike_ratio)."""
        scored = []
        for t in topics:
            title = t.get("title", "")
            metrics = fetch_pageview_metrics(title)
            score = metrics.get("views_30d", 0) * metrics.get("spike_ratio", 1.0)
            scored.append((score, {**t, "pageview_metrics": metrics}))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored]

