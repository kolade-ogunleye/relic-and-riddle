"""
src/media_budget.py
===================
Spend guard for pay-per-use media APIs (AI video now; anything billed per second or per
item later). Every paid call asks `allow(cost)` first and `record(...)` after, so a bug or
a retry loop can never run up an unbounded bill. MEDIA_MONTHLY_BUDGET_USD = 0 disables
paid media APIs entirely (the free fallbacks still run).
"""

import logging
from datetime import datetime, timezone

from . import config
from .db import PipelineDatabase

log = logging.getLogger(__name__)


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def spent(db: PipelineDatabase) -> float:
    return db.media_spend(_month())


def allow(db: PipelineDatabase, estimated_cost: float) -> bool:
    budget = config.MEDIA_MONTHLY_BUDGET_USD
    if budget <= 0:
        return False
    total = spent(db)
    if total + estimated_cost > budget:
        log.warning("Media budget: $%.2f spent of $%.2f; a $%.2f call would exceed it.", total, budget, estimated_cost)
        return False
    return True


def record(db: PipelineDatabase, provider: str, item: str, cost: float) -> None:
    try:
        db.record_media_usage(provider, item, cost)
    except Exception as e:  # accounting must never break a run
        log.warning("Could not record media usage: %s", e)
