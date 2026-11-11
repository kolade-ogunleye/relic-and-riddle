"""Recovery freshness as the pipeline sees it: the backup worker's redacted status receipt, never its spool or keys.

The worker (a separate process and, in production, a separate account) writes ``config.BACKUP_STATUS_FILE`` at the
end of every cycle (src/backup_checkpoints.py). This module turns its timestamps into the operator's line,
"last snapshot X h / last drill N d / hourly copy M min", and into Needs-you alerts:
- backup stale: no cloud-verified daily snapshot in 26 h (the off-machine recovery point);
- restore drill overdue: no cloud-sourced isolated restore in 30 days;
- hourly copy stale: no verified hourly SQLite copy in 3 h (one missed hour plus slack, so one slow cycle never pages);
- backup worker fault (<stage>): the worker's last cycle named a stage that failed (hourly copy, retention), so a
  lasting fault reaches the operator even when the ages still look fresh.
A missing, unreadable or foreign receipt counts as unknown, which alerts: silence must not look like health.
"""
import html
import json
import logging
from datetime import UTC, datetime, timedelta

from . import config

log = logging.getLogger(__name__)

SNAPSHOT_HOURS = 26
DRILL_DAYS = 30
HOURLY_HOURS = 3
NOTICE_KEY = "backup_health_notice"
FAULT_STAGES = {"hourly": "hourly copy", "retention": "retention"}  # the receipt's names; anything else is ignored


def monitored() -> bool:
    """Whether this environment has a backup worker whose freshness the operator must see (production always; an
    environment configured for the API transport too). Development's desktop-folder copy has no worker."""
    return config.BACKUP_ENVIRONMENT == "production" or config.BACKUP_TRANSPORT == "onedrive-api"


def _stamp(value, current):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if not parsed.tzinfo:
        return None
    parsed = parsed.astimezone(UTC)
    return parsed if parsed <= current + timedelta(minutes=5) else None  # a future time is not evidence


def read_receipt() -> dict:
    try:
        path = config.BACKUP_STATUS_FILE
        if path.stat().st_size > 8192:
            return {}
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(receipt, dict) or receipt.get("schema") != 1 or receipt.get("environment") != config.BACKUP_ENVIRONMENT:
        return {}
    return receipt


def _hours(value):
    if value is None:
        return "unknown"
    hours = value.total_seconds() / 3600
    return f"{max(0, round(hours * 60))} min" if hours < 1 else f"{hours:.0f} h"


def health(*, now_at=None) -> dict:
    """{"monitored", "line", "alerts"}; the line is plain text (escape it for HTML)."""
    if not monitored():
        return {"monitored": False, "line": "No backup worker runs in this environment", "alerts": []}
    current = now_at or datetime.now(UTC)
    receipt = read_receipt()
    ages = {}
    for key in ("snapshot_at", "drill_at", "database_at"):
        made = _stamp(receipt.get(key), current)
        ages[key] = None if made is None else max(timedelta(0), current - made)
    snapshot, drill, hourly = ages["snapshot_at"], ages["drill_at"], ages["database_at"]
    alerts = []
    if snapshot is None or snapshot > timedelta(hours=SNAPSHOT_HOURS):
        alerts.append("backup stale")
    if drill is None or drill > timedelta(days=DRILL_DAYS):
        alerts.append("restore drill overdue")
    if hourly is None or hourly > timedelta(hours=HOURLY_HOURS):
        alerts.append("hourly copy stale")
    errors = receipt.get("errors")
    faults = sorted({FAULT_STAGES[name] for name in errors if isinstance(name, str) and name in FAULT_STAGES}) if isinstance(errors, list) else []
    if faults:
        alerts.append("backup worker fault (" + ", ".join(faults) + ")")
    drill_label = "unknown" if drill is None else f"{drill.total_seconds() / 86400:.0f} d"
    line = f"Last snapshot {_hours(snapshot)} / last drill {drill_label} / hourly copy {_hours(hourly)}"
    if not receipt:
        line += " (no status from the backup worker)"
    return {"monitored": True, "line": line, "alerts": alerts}


def status_lines(*, now_at=None) -> list:
    """The /status and heartbeat section (HTML)."""
    result = health(now_at=now_at)
    text = "🗄 " + html.escape(result["line"])
    if result["alerts"]:
        text += "\n⚠️ " + html.escape("; ".join(result["alerts"]))
    return ["", "<b>Recovery</b>", text]


def dashboard_line(*, now_at=None):
    """One dashboard line while something needs attention; nothing while backups are healthy."""
    result = health(now_at=now_at)
    if not result["alerts"]:
        return None
    return "🗄 ⚠️ " + html.escape("; ".join(result["alerts"]) + " · " + result["line"])


def notify_stale(db, bot, *, now_at=None) -> bool:
    """One Needs-you notice per UTC day for a given set of alerts; a failed delivery is retried at the next check."""
    current = now_at or datetime.now(UTC)
    result = health(now_at=current)
    if not result["alerts"]:
        if db.get_setting(NOTICE_KEY):
            db.set_setting(NOTICE_KEY, "")
        return False
    signature = current.date().isoformat() + ":" + ",".join(result["alerts"])
    if db.get_setting(NOTICE_KEY) == signature:
        return False
    text = ("🗄 <b>Backups need attention</b>: " + html.escape("; ".join(result["alerts"])) + "\n"
            + html.escape(result["line"]) + "\nRecovery steps and the restore drill: docs/backup.md.")
    try:
        sent = bot.notify(text, group="needs")
    except Exception as exc:  # noqa: BLE001 - an alert must never break the daemon's scheduler
        log.warning("Backup notice not sent: %s", type(exc).__name__)
        return False
    if sent:
        db.set_setting(NOTICE_KEY, signature)
        return True
    return False
