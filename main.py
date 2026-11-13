"""
main.py
=======
CLI orchestrator and daily scheduler for the Relic & Riddle Reels pipeline.

Usage:
  python main.py run [--topic-id ID] [--auto-approve] [--dry-run]
  python main.py resume <run_id> [--auto-approve] [--dry-run]   (continue a failed run without re-paying)
  python main.py daemon [--time 09:00]
  python main.py bot                                          (Telegram commands only; no scheduled runs)
  python main.py update [--apply]                             (outdated / vulnerable packages; test-gated apply)
  python main.py policy [--telegram]                          (new Meta policy changes, sourced)
  python main.py review list
  python main.py review approve|reject|regenerate <run_id>   (while a run is waiting)
  python main.py review publish <run_id> [--dry-run]         (publish a finished, unpublished run)
  python main.py topic next | list | requeue <topic_id> | repair | propose | add <slug>
  python main.py assets list | approve <topic_id>            (review fetched scene images)
  python main.py health [--live]
  python main.py test                                        (end-to-end dry run, nothing published)
"""

import argparse
import concurrent.futures
import contextlib
import html
import json
import re
import os
import shutil
import subprocess
import threading
import logging
import sys
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config import ASSETS_DIR, CURATED_SCRIPTS_DIR, DAILY_RUN_TIME, OUTPUT_DIR, POST_SCHEDULING, TIMEZONE
from src.credits import caption_credit_line, generate_provenance_ledger, load_credits
from src.db import REVIEW_CLAIM_PREFIX, UNDONE_BY, UNDOABLE, PipelineDatabase, QueueEmpty
from src.control import clip_words, heartbeat_text, is_paused, make_command_handler, ping_healthcheck, record_run_mode
from src import beat_images, callouts, colour_balance, config, encoding, footage, graphics, motion, notifications, post_schedule, privacy, then_now, worker_deadline
from src.llm import claude_available as llm_available
from src.scene_planner import degraded_note, documentary_cuts, fit_findings, fit_note, fit_to_lines, plan_scenes, segment_beats
from src.topic_proposer import origin_note as topic_origin_note
from src.upscale import ensure_all, ensure_resolution
from src.music import music_for, music_note, music_selection_rows
from src.parallax import render_scenes as render_parallax
from src.video_composer import TRANS_DUR, caption_cut_times
from src.video_qa import QualityAuditor, audit_scene_feedback, qa_summary_html, review_render
from src.control import failed_heading
from src.ops import AssetLifecycleManager, PreFlightHealthChecker, setup_logging
from src.fact_sheet import FactStarvedTopicError
from src.stage_timer import StageTimer
from src.improve import REASONS, SCRIPT_REASONS
from src.operator_jobs import JobCancelled

log = logging.getLogger("pipeline")

MAX_REGENERATIONS = 2
# Ends of a run that are outcomes, not failures (the run command exits 0 for these).
RUN_COMPLETED_STATUSES = ("published", "simulated", "scheduled", "rejected", "regenerate", "timeout",
                          "cancelled", "publish_unknown")
DOCUMENTARY_PAUSED = ("The 16:9 documentary is paused while it is rebuilt (add \"documentary\" to VIDEO_FORMATS "
                      "to make one; see docs/documentary-rebuild.md).")
TAKE_SPREAD = 1.10  # how far apart two ElevenLabs takes of one script ran (production 2026-10-25: 0.997-1.100)
MAX_FACT_REVISIONS = 2  # rewrites of a Claude script that failed fact-checking, before failing closed


MISSED_SLOT_GRACE = timedelta(hours=3)
SCHEDULED_POSTING_LOCK = "scheduled-posting"  # one post_due pass at a time across processes


def compute_act_chapters(acts: List[Any], words: List[Dict[str, Any]], total_dur: float) -> Tuple[List[Dict[str, Any]], str, List[float]]:
    """
    Computes exact start times and mm:ss formatted chapter marks for documentary acts.
    Returns:
      - chapters: list of dicts with act_number, title, start_sec, timestamp_str
      - formatted_text: YouTube/Facebook formatted timestamp block
      - cue_timestamps: list of act transition seconds (> 0)
    """
    chapters = []
    cue_timestamps = []
    w_idx = 0
    total_words = len(words)

    for i, act in enumerate(acts):
        act_num = getattr(act, "act_number", None) or (act.get("act_number") if isinstance(act, dict) else i + 1)
        act_title = getattr(act, "act_title", None) or (act.get("act_title") if isinstance(act, dict) else f"Act {act_num}")
        narration = getattr(act, "narration_text", None) or (act.get("narration_text") if isinstance(act, dict) else "")

        if i == 0 or not words:
            start_sec = 0.0
        else:
            tokens = [re.sub(r"[^\w]", "", w).lower() for w in narration.split()[:5] if re.sub(r"[^\w]", "", w)]
            matched_time = None
            if tokens:
                for scan_i in range(w_idx, total_words):
                    scan_word = re.sub(r"[^\w]", "", words[scan_i]["word"]).lower()
                    if scan_word == tokens[0]:
                        if len(tokens) > 1 and scan_i + 1 < total_words:
                            next_word = re.sub(r"[^\w]", "", words[scan_i + 1]["word"]).lower()
                            if next_word == tokens[1]:
                                matched_time = words[scan_i]["start"]
                                w_idx = scan_i
                                break
                        else:
                            matched_time = words[scan_i]["start"]
                            w_idx = scan_i
                            break
            if matched_time is not None:
                start_sec = matched_time
            else:
                start_sec = round(total_dur * (i / max(1, len(acts))), 2)

        minutes = int(start_sec) // 60
        seconds = int(start_sec) % 60
        ts_str = f"{minutes}:{seconds:02d}"

        chapters.append({
            "act_number": act_num,
            "title": act_title,
            "start_sec": round(start_sec, 2),
            "timestamp": ts_str,
        })
        if start_sec > 0:
            cue_timestamps.append(start_sec)

    lines = ["CHAPTERS:"]
    for ch in chapters:
        lines.append(f"{ch['timestamp']} Act {ch['act_number']} - {ch['title']}")
    formatted_text = "\n".join(lines)

    return chapters, formatted_text, cue_timestamps


def documentary_cover(title: str, max_line: int = 22) -> Dict[str, str]:
    """Documentary cover text: the fact-checked title over two lines split on whole words (the first line up to
    max_line characters, the rest of the title whole on the second), with an eyebrow and tagline that make no
    factual claim. QF-23: never cut; it was cut mid-word with an ellipsis at max_line characters. A title the
    cover cannot set whole is refused there (cover_design's strict fit, typography.TextFitError)."""
    words, line1 = title.upper().split(), ""
    while words and (not line1 or len(f"{line1} {words[0]}") <= max_line):
        line1 = f"{line1} {words.pop(0)}".strip()
    return {"eyebrow": "DOCUMENTARY", "line1": line1, "line2": " ".join(words), "tagline": "EXPLORE THE EVIDENCE"}


def warn_missing_geo_files() -> List[str]:
    """A WARNING at run start for each map data file not on disk (tools/ is gitignored, so a deploy from GitHub
    has neither until they are fetched): without the country outlines no locator or route map is made, without the
    shaded relief they render flat. Returns the warnings logged."""
    from src import geo
    lines = []
    for path, command in geo.missing_files():
        try:
            shown = path.relative_to(config.BASE_DIR)
        except ValueError:
            shown = path
        effect = "maps render flat (no shaded relief)" if "relief" in path.parts else "no map graphics are made"
        lines.append(f"Map data missing: {shown} is not on disk, so {effect}. Fetch it with: {command}")
        log.warning(lines[-1])
    return lines


def warn_missing_raqm() -> List[str]:
    """A WARNING at run start when Pillow cannot lay text out with raqm (operator decision 2026-11-05: raqm everywhere,
    so every machine renders what review measured). Pillow's wheels load FriBiDi at run time: on Linux the libfribidi0
    package, on Windows libfribidi-0.dll on PATH (docs/HANDOVER-design.md). Without it text falls back to BASIC layout
    (no shaping, no GPOS kerning) and lines measure wider than reviewed. Returns the warnings logged."""
    from PIL import features
    if features.check("raqm"):
        return []
    line = ("Pillow raqm text layout is unavailable (FriBiDi not found), so text renders with BASIC layout and "
            "measures wider than reviewed. Install FriBiDi: Linux libfribidi0; Windows libfribidi-0.dll on PATH "
            "(docs/HANDOVER-design.md).")
    log.warning(line)
    return [line]


class PipelineCoordinator:
    def __init__(self, db: Optional[PipelineDatabase] = None, schedule_posts: bool = False):
        # schedule_posts: approved Reels wait for their posting slot (daemon); otherwise post at once (CLI).
        self.schedule_posts = schedule_posts
        self.only_promoted = False  # a dry-run daemon: booked posts fire only for runs promoted with "Post for real"
        self.paid_voice_in_dry_run = False  # `--paid-voice`: this dry run may use ElevenLabs
        self.paid_motion_in_dry_run = False  # `--paid-motion`: this dry run may buy a motion clip
        self.reuse_from: Optional[str] = None  # `--reuse-from`: an earlier run of the same topic whose work is reused
        # Imported here so `health`/`topic`/`review` work even if a media dependency is broken.
        from src.audio_engine import AudioEngine
        from src.multi_format import MultiFormatGenerator
        from src.publisher import MetaPublisher
        from src import llm
        from src.script_engine import ScriptEngine
        from src.telegram_bot import TelegramApprovalBot
        from src.topic_engine import TopicEngine
        from src.video_composer import VideoComposer
        from src.visual_engine import VisualEngine

        self.db = db or PipelineDatabase()
        llm.configure_usage(self.db.record_llm_usage, self.db.llm_spend)
        self.health_checker = PreFlightHealthChecker()
        self.lifecycle_manager = AssetLifecycleManager(db=self.db)   # never a pending run's files (QF-110)
        self.topic_engine = TopicEngine(db=self.db)
        self.script_engine = ScriptEngine()
        self.audio_engine = AudioEngine()
        self.visual_engine = VisualEngine()
        self.video_composer = VideoComposer()
        self.multiformat_gen = MultiFormatGenerator()
        self.telegram_bot = TelegramApprovalBot(db=self.db)
        self.telegram_bot.approval_window = self._approval_window  # the review card says when it closes (QF-43)
        self.telegram_bot.approve_posting = self._approve_posting  # ...and whether Approve books a slot
        self.publisher = MetaPublisher(db=self.db)

    def run_cycle(
        self,
        topic_id: Optional[str] = None,
        auto_approve: bool = False,
        dry_run: bool = False,
        require_live: bool = False,
        content_format: str = "reel",
        _regenerations: int = 0,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        # VIDEO_FORMATS decides what runs: a request is narrowed to the listed formats before anything is spent.
        allowed = config.effective_format(content_format)
        if allowed is None:
            log.info("Run declined: %s is not in VIDEO_FORMATS (%s).", content_format, ",".join(config.VIDEO_FORMATS))
            return {"status": "paused", "topic_id": topic_id, "reason": f"{content_format} is not in VIDEO_FORMATS"
                    + (f". {DOCUMENTARY_PAUSED}" if content_format == "deep-dive" else "")}
        if allowed != content_format:
            log.info("Run format %s narrowed to %s by VIDEO_FORMATS.", content_format, allowed)
        content_format = allowed
        health = self.health_checker.run_all(require_live=require_live)
        for k, v in health["details"].items():
            log.info("Health %-11s %s %s", k, "OK " if v["passed"] else ("FAIL" if v["required"] else "warn"), v["msg"])
        if config.FONT_SIZE == 72:   # the caption size before 2026-11-03, most likely an old .env line
            log.warning("FONT_SIZE=72 is set (a .env from before 2026-11-03?): Reel captions draw at 72 px, not the "
                        "approved 80. Remove the line or set FONT_SIZE=80.")
        warn_missing_geo_files()
        warn_missing_raqm()
        if not health["all_passed"]:
            # Said in Telegram, not only logged: production's scheduled runs failed this way unnoticed (ffprobe).
            failed = [f"{k}: {v['msg']}" for k, v in health["details"].items() if v["required"] and not v["passed"]]
            try:
                _notice(self, "🩺 <b>Run not started: a pre-flight check failed</b>\n\n"
                            + "\n".join(f"• {html.escape(line[:200])}" for line in failed)
                            + "\n\nNothing was made or spent. Fix the check, then start the run again.", section="needs", key="preflight",
                             actions=[{"text": "Details", "callback_data": "cstat:"}])
            except Exception as e:
                log.warning("Could not send the pre-flight alert: %s", e)
            ping_healthcheck("/fail")
            raise SystemExit("Pre-flight health check failed; not starting a run.")
        self.lifecycle_manager.purge_intermediate_assets()

        if topic_id:
            topic = self.topic_engine.get_topic_by_id(topic_id)
            if not topic:
                raise ValueError(f"Topic ID '{topic_id}' not found in topic bank.")
            if topic.get("hold"):   # QF-73: an editorial hold is cleared in the bank, never bypassed by naming it
                raise ValueError(f"Topic '{topic_id}' is on hold: {topic['hold']}")
        else:
            # The first topic in production order whose photo pool is approved, or can be made so now
            # (fetched and reviewed on merit); topics without usable photos are skipped, not failed.
            from src.asset_pipeline import first_ready
            skipped = []
            # Topics whose approved pool has too few distinct authoritative photos go behind the others, said with
            # their reason when one would otherwise have been made first (src/topic_engine.demote_thin, 2026-11-02).
            ranked = self.db.ranked_topics()
            order = {t["id"]: i for i, t in enumerate(ranked)}
            try:
                from src.topic_engine import demote_thin
                ranked, thin = demote_thin(ranked)
            except (OSError, ValueError, TypeError, KeyError) as e:
                log.warning("Topic image gate could not run (%s); production order unchanged.", e)
                thin = []
            topic = first_ready(ranked, skipped=skipped)
            passed_over = [(t, why) for t, why in thin if topic is not None and t["id"] != topic["id"]
                           and order.get(t["id"], 0) < order.get(topic["id"], 0)]
            if passed_over:
                try:
                    _notice(self, "🖼 <b>Topics moved back: too few photos for every line</b>\n\n"
                                + "\n".join(f"• {html.escape(t.get('title') or t['id'])}: {html.escape(why)}"
                                            for t, why in passed_over[:8]), section="info", key="photos-skip")
                except (OSError, RuntimeError, ValueError) as e:   # requests' errors are OSErrors
                    log.warning("Could not send the topic image gate alert: %s", e)
            if skipped:  # said, with the video's own count: an approved set it could not use failed at start (audit 2026-10-28)
                try:
                    _notice(self, "🖼 <b>Topics skipped: not enough photos a video can use</b>\n\n"
                                + "\n".join(f"• {html.escape(t.get('title') or t['id'])}: {html.escape(why)}"
                                            for t, why in skipped[:8]), section="info", key="photos-skip")
                except Exception as e:
                    log.warning("Could not send the skipped-topics alert: %s", e)
            if topic is None:
                raise QueueEmpty("No queued topic has an approved photo pool; see `python main.py assets review`.")
        tid = topic["id"]
        # Where the topic goes after a simulated publish: back to the queue unless it has really been
        # published (as resume_run already did). Inheriting the pre-run status sent an approved regeneration's
        # topic back to "rejected" (2026-10-24).
        previous_status = "published" if topic.get("times_published") else "queued"

        if content_format == "deep-dive":
            return self._review_documentary(self.produce_deep_dive(topic, parent_run_id=None, auto_approve=auto_approve,
                                                                   dry_run=dry_run, run_id=run_id), dry_run)

        run_id = run_id or (f"run_{tid}_{int(time.time())}" + ("_short" if content_format == "dual" else ""))
        run_dir = OUTPUT_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        from src.publication_lock import publication_lock
        with publication_lock(self.db.db_path, "topic-images:" + tid):
            self.db.create_run(run_id, tid, content_format="reel")
            self.db.set_setting("run_mode:" + run_id, "dry_run" if dry_run else "live")
            claim_run(self.db, run_id)  # this process makes it, reviews it and acts on it (resume_reviews)
            self.topic_engine.record_status(tid, "in_production", run_id=run_id)
        log.info("Run %s: topic '%s'", run_id, topic["title"])
        if _regenerations:  # its place in a chain of regenerations (read by run --regenerate-from)
            self.db.set_setting("regenerations:" + run_id, str(_regenerations))

        from src.ops import RunLog
        sink = RunLog(run_dir).start()  # the run's own DEBUG log and model-call record (output/<run>/)
        try:
            result = self._produce_and_publish(topic, run_id, run_dir, auto_approve, dry_run, previous_status)
            self._finish_progress(topic.get("title", tid), run_id, success=True)
            self._collect_findings(run_id, run_dir, tid)
        except worker_deadline.RunDeadlineExceeded as e:  # stopped at its deadline (_stop_at_deadline), files kept
            self._collect_findings(run_id, run_dir, tid, error=f"RunDeadlineExceeded: {e}")
            return {"status": "timed_out", "run_id": run_id, "topic_id": tid}
        except JobCancelled:
            return {"status": "cancelled", "run_id": run_id, "topic_id": tid}
        except FactStarvedTopicError as e:
            self._collect_findings(run_id, run_dir, tid, error=f"FactStarvedTopicError: {e}")
            if getattr(self, "progress_msg_id", None):
                try:
                    self.telegram_bot.finish_progress_card(self.progress_msg_id, topic["title"], run_id, success=False, error="Fact-starved (insufficient evidence)")
                except Exception:
                    pass
                self.progress_msg_id = None
            self.db.update_run(run_id, "failed", error=f"FactStarvedTopicError: {e}")
            self.topic_engine.record_status(tid, "fact_starved", run_id=run_id)
            log.warning("Topic '%s' is fact-starved: %s. Gracefully skipping to protect pipeline integrity.", tid, e)
            _notice(self, f"⚠️ <b>Topic skipped: fact-starved</b>\n\n<code>{html.escape(tid)}</code>\n\n{html.escape(str(e))}\n\nSkipped gracefully to next queued topic.", section="info")
            next_t = self.topic_engine.get_next_topic()
            if next_t and next_t["id"] != tid:
                log.info("Auto-advancing to next topic '%s'...", next_t['id'])
                return self.run_cycle(next_t["id"], auto_approve, dry_run, require_live, content_format)
            return {"status": "skipped", "reason": "fact_starved", "topic_id": tid}
        except Exception as e:
            self.db.update_run(run_id, "failed", error=f"{type(e).__name__}: {e}")
            self.topic_engine.record_status(tid, "failed", run_id=run_id)
            card_shown = False
            if getattr(self, "progress_msg_id", None):
                try:  # the progress card becomes the run's one failure card
                    card_shown = bool(self.telegram_bot.finish_progress_card(self.progress_msg_id, topic["title"], run_id, success=False, error=str(e)))
                except Exception:
                    pass
                self.progress_msg_id = None
            self._collect_findings(run_id, run_dir, tid, error=f"{type(e).__name__}: {e}")
            log.error("Run %s failed: %s", run_id, e)
            if not card_shown:
                _notice(self, f"❌ <b>{failed_heading(self.db, run_id)}</b>\n\n<b>{html.escape(self.db.run_label(run_id))}</b>\n<blockquote expandable>{html.escape(str(e)[:600])}</blockquote>\nNothing was posted. The topic is set aside: restore it from the queue (/queue requeue N).", section="needs", key=f"fail:{run_id}", run=run_id,
                             actions=notifications.failure_actions(self.db, run_id))
            ping_healthcheck("/fail")
            raise
        finally:
            sink.stop()
            release_run(self.db, run_id)

        if result["status"] == "regenerate":
            if _regenerations < MAX_REGENERATIONS:
                log.info("Regeneration requested; starting a fresh run for '%s'.", tid)
                self._operator_feedback = self._await_reason(run_id)
                try:
                    return self.run_cycle(tid, auto_approve, dry_run, require_live, content_format, _regenerations + 1)
                finally:
                    self._operator_feedback = None
            log.warning("Regeneration limit reached for '%s'.", tid)
            self.topic_engine.record_status(tid, "failed", run_id=run_id)

        if content_format == "dual":
            log.info("Dual-format requested; producing child 16:9 widescreen documentary...")
            try:
                deep_res = self._review_documentary(
                    self.produce_deep_dive(topic, parent_run_id=run_id, auto_approve=auto_approve, dry_run=dry_run), dry_run)
                result["deep_dive_run"] = deep_res
                result["dual_format"] = True
            except Exception as e:
                log.error("Dual-format deep dive child run failed: %s", e)
                result["deep_dive_run"] = {"status": "failed", "error": str(e)}

        return result

    def _check_cancel(self, run_id):
        """A stage boundary: an operator's cancel, or the run deadline (src/worker_deadline.py), stops the run here."""
        if worker_deadline.stopped_here(run_id):  # already stopped: a stage that swallowed the stop meets it again
            raise worker_deadline.RunDeadlineExceeded(worker_deadline.stop_reason(config.RUN_DEADLINE_MINUTES))
        if worker_deadline.overdue(run_id):
            self._stop_at_deadline(run_id)
        if not self.db.get_setting("cancel_run:" + run_id):
            return
        run = self.db.get_run(run_id)
        if not run or run["status"] in {"publishing", "publish_unknown", "published"}:
            return
        self.db.update_run(run_id, "cancelled", error="Cancelled by operator at a stage boundary")
        self.topic_engine.record_status(run["topic_id"], "cancelled", run_id=run_id)
        self._finish_progress(self.db.run_label(run_id), run_id, False, "Cancelled by operator")
        raise JobCancelled("Cancelled by operator")

    def _stop_at_deadline(self, run_id: str) -> None:
        """Past its production deadline at a stage boundary: a run still being made stops as timed_out, keeping
        every file it made; its topic goes back in the queue (unless another run of it is booked or posted: a dual
        run's Reel), its progress card says why, one Needs-you notice."""
        minutes = config.RUN_DEADLINE_MINUTES
        reason = worker_deadline.stop_reason(minutes)
        with self.db._conn() as conn:  # only from producing: a review, an upload or a cancel is never overwritten
            stopped = conn.execute("UPDATE runs SET status = 'timed_out', error = ?, updated_at = ? "
                                   "WHERE run_id = ? AND status = 'producing'",
                                   (reason + " and stopped at a stage boundary.", datetime.now(UTC).isoformat(),
                                    run_id)).rowcount
        if not stopped:
            return
        worker_deadline.mark_stopped(run_id)
        run = self.db.get_run(run_id) or {}
        log.warning("Run %s %s; stopped at a stage boundary, files kept.", run_id, reason.lower())
        requeued = worker_deadline.requeue_topic(self.db, run)
        label = self.db.run_label(run_id)
        self._finish_progress(label, run_id, False, reason)
        worker_deadline.notice_once(self.db, run_id, worker_deadline.deadline_notice(
            label, minutes, worker_deadline.kept_line(run_id, run), requeued=requeued), self._alert)
        raise worker_deadline.RunDeadlineExceeded(reason)

    def _progress_note(self, run_id: str, title: str, step: int, text: str) -> None:
        """A word on the progress card about the long sub-stage under way (the card went 5.6 minutes
        without a change through motion, parallax and the master render: production, 2026-10-24).
        Never fails the run."""
        if not getattr(self, "progress_msg_id", None):
            return
        try:
            self.telegram_bot.update_progress_card(self.progress_msg_id, step, 5, title, run_id, text)
        except Exception as e:
            log.debug("Progress note not shown: %s", e)

    def _finish_progress(self, title: str, run_id: str, success: bool, error: str = "") -> None:
        """Ends the run's progress card and its 'in production' record (idempotent)."""
        mid, self.progress_msg_id = getattr(self, "progress_msg_id", None), None
        try:
            if mid:
                self.telegram_bot.finish_progress_card(mid, title, run_id, success=success, error=error)
            active = self.db.get_active_progress() if hasattr(self.db, "get_active_progress") else None
            if active and active.get("run_id") == run_id:
                if success and hasattr(self.db, "record_progress_history"):
                    self.db.record_progress_history(run_id)  # each step's length, for the next estimates
                self.db.clear_active_progress()
        except Exception as e:
            log.debug("Could not finish the progress card: %s", e)

    def _collect_findings(self, run_id: str, run_dir: Path, topic_id: str, error: Optional[str] = None) -> None:
        """Files what this run found into the improvement ledger (never fails the run)."""
        try:
            from src import improve
            run = self.db.get_run(run_id) or {}
            n = improve.collect_run(self.db, run_id, run_dir, run.get("script_payload"), topic_id=topic_id, error=error)
            from src.stage_timer import file_findings
            n += file_findings(self.db, run_id, run_dir) if run_dir else 0  # a stage over 150% of its budget
            log.info("Improvement ledger: %d finding(s) recorded for %s.", n, run_id)
        except Exception as e:
            log.warning("Could not record findings for %s: %s", run_id, e)

    # The documentary's targets (docs/documentary-rebuild.md): what the audit ledger checks it against.
    DOC_MIN_SECONDS, DOC_MAX_STILL_SECONDS = 8 * 60, 12.0

    @classmethod
    def _audit_documentary(cls, seconds: float, acts: int, scenes: List[Path], durations: List[float]) -> None:
        """Notes where a documentary falls short of its targets, for the audit ledger (never fails the run)."""
        from src import improve
        from src.typography import is_video
        if seconds < cls.DOC_MIN_SECONDS:
            improve.note("documentary.length", f"{seconds / 60:.1f} min; the target is 8-15 min "
                         "(mid-roll ads need 8+)", source="documentary")
        graphics = {Path(s).name for s in scenes if Path(s).stem.startswith("graphic_")}
        if len(graphics) < acts:
            improve.note("documentary.graphics", f"{len(graphics)} original graphic(s) for {acts} acts; "
                         "the target is at least one per act", source="documentary")
        static = [d for s, d in zip(scenes, durations) if not is_video(Path(s)) and d > cls.DOC_MAX_STILL_SECONDS]
        if static:
            improve.note("documentary.static_stills", f"{len(static)} still(s) held over "
                         f"{cls.DOC_MAX_STILL_SECONDS:.0f}s (longest {max(static):.0f}s)", source="documentary")

    def _await_reason(self, run_id: str, wait_seconds: int = 120, detail_seconds: Optional[int] = None) -> Dict[str, Any]:
        """After Regenerate, waits briefly for your reason chip (and words) so the new run can act on it.
        Under "Other" the words decide what is kept (revises_script), so it waits up to detail_seconds more for
        them, ending as soon as they come (production 2026-10-26, Roman concrete: Other at 14:22:41, the run
        began at 14:22:45, the words came at 14:22:52, and the script was kept word for word three runs in a
        row). The decided card then says what the new version keeps and redoes."""
        from src import improve
        from src.telegram_bot import REGEN_DETAIL_SECONDS
        live = self.telegram_bot.is_configured()
        deadline = time.monotonic() + (wait_seconds if live else 0)
        fb = improve.operator_feedback(self.db, run_id)
        while fb is None and time.monotonic() < deadline:
            time.sleep(3)
            fb = improve.operator_feedback(self.db, run_id)
        if fb and fb.get("reason") == "other" and not fb.get("text") and live:
            # Counted from the Regenerate tap, so a regeneration started long after it waits only briefly.
            words_by = time.monotonic() + (reason_window(self.db, run_id, REGEN_DETAIL_SECONDS)
                                           if detail_seconds is None else detail_seconds)
            log.info("Reason Other without words; waiting up to %.0fs for them before deciding what to keep.",
                     max(0.0, words_by - time.monotonic()))
            while not (fb or {}).get("text") and time.monotonic() < words_by:
                time.sleep(3)
                fb = improve.operator_feedback(self.db, run_id) or fb
        fb = dict(fb or {})
        fb["previous"] = (self.db.get_run(run_id) or {}).get("script_payload")
        fb["from_run"] = run_id  # so later steps can read details added after the run began (_feedback)
        if fb.get("reason"):
            log.info("Regenerating with your feedback: %s %s", fb["reason"], fb.get("text", ""))
        plan = regeneration_plan(fb)
        log.info("Regeneration of %s: %s", run_id, re.sub(r"<[^>]+>", "", plan))
        try:
            self.telegram_bot.show_regeneration_plan(run_id, fb, plan)
        except Exception as e:  # the card is a courtesy; the regeneration goes ahead regardless
            log.debug("Regeneration plan not shown on the card: %s", e)
        return fb

    def _feedback(self) -> Dict[str, Any]:
        """This regeneration's feedback as it stands now: a reason or details added (a reply to the reason
        card) after the run began still reach the steps that have not run yet."""
        fb = dict(getattr(self, "_operator_feedback", None) or {})
        if fb.get("from_run"):
            from src import improve
            fb.update({k: v for k, v in (improve.operator_feedback(self.db, fb["from_run"]) or {}).items() if v})
        return fb

    def _previous_run_dir(self) -> Optional[Path]:
        """The run this regeneration replaces (Regenerate from its review), or the one this run was told to
        reuse (--reuse-from), if its folder is still there."""
        previous = self._feedback().get("from_run") or getattr(self, "reuse_from", None)
        if previous and (OUTPUT_DIR / previous).is_dir():
            return OUTPUT_DIR / previous
        return None

    def _script_from_previous(self, fb: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The verified script of the run a regeneration replaces, when the review was not about the words
        (pictures, sound, cover, other): the script-stage payload as it was saved, guardrail passed. None
        when the reason concerns the script, there is no previous run, or its saved script did not pass."""
        from src.script_library import reuses
        reason = fb.get("reason")
        if not reuses("script"):
            return None
        if fb.get("from_run"):
            if not reason or reason in SCRIPT_REASONS or revises_script(fb):
                return None
        elif not getattr(self, "reuse_from", None):  # --reuse-from: the script that run passed, as it was
            return None
        previous = self._previous_run_dir()
        if previous is None:
            return None
        try:
            saved = json.loads((previous / "verified_script.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not (isinstance(saved, dict) and saved.get("guardrail_passed") and saved.get("narration_script")):
            return None
        return saved if PipelineCoordinator._passes_today(saved, previous.name) else None

    @staticmethod
    def _passes_today(saved: Dict[str, Any], origin: str) -> bool:
        """A kept script must pass today's checks, not only those it passed when written (2026-10-25: a kept
        script carried the repeated "By one estimate" past the check added after it was written)."""
        from src.models import ScriptPackage
        from src.script_engine import spoken_length_problems, validate_structure
        try:
            # The spoken length too, at the voice's current measured pace: a script whose take ran over is
            # shortened rather than voiced again unchanged (2026-10-25).
            kept_pkg = ScriptPackage.model_validate(saved)
            problems = validate_structure(kept_pkg) or spoken_length_problems(kept_pkg)
        except Exception as e:
            problems = [f"the saved script no longer reads as a script package ({type(e).__name__})"]
        if problems:
            log.info("Script of %s not kept: it fails today's checks (%s).", origin, problems[0][:160])
            return False
        return True

    def _script_from_library(self, topic: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The script shipped in the script library for this topic, when it was checked against this very fact
        sheet and passes today's checks (development's step 1, not paid for again: operator, 2026-10-27)."""
        from src import config as pipeline_config, script_library
        saved = script_library.kept_script(topic["id"], pipeline_config.DATA_DIR / "fact_sheets" / f"{topic['id']}.json")
        if saved is None:
            return None
        from src.models import ScriptPackage
        from src.script_engine import validate_structure
        try:
            problems = validate_structure(ScriptPackage.model_validate(saved))
        except Exception as e:
            problems = [f"it no longer reads as a script package ({type(e).__name__})"]
        if problems:
            log.info("Library script for %s not used: it fails today's checks (%s).", topic["id"], problems[0][:160])
            return None
        # Too long for this environment's narrator (production's ElevenLabs reads slower than development's
        # measurement: three library scripts ran 51.8-53.7 s for a 51.5 s limit, 2026-10-27): the caller shortens
        # it to the measured word count and fact-checks the revision, instead of paying for a new script.
        saved["_too_long_here"] = self._spoken_length_now(saved)
        # QF-14: a hook fault blocks. A library script written before the rule gets the one opening edit, fact-checked
        # again as any revision is, the same way (all four shipped entries open on 17-26 words, 2026-11-10).
        # QF-15: so does a payoff fault, in the same one edit (no shipped entry has a payoff_sentence).
        # QF-16: and a hedge in the opening, payoff or loop, read against the topic's own sheet.
        from src import fact_sheet
        from src.script_engine import (
            hedge_problems,
            hook_problems,
            opening_edit_findings,
            payoff_problems,
        )
        hook = (hook_problems(saved.get("narration_script") or "")
                + payoff_problems(saved.get("narration_script") or "", saved.get("payoff_sentence") or "")
                + hedge_problems(saved.get("narration_script") or "", fact_sheet.load(topic["id"], max_age_days=3650)))
        saved["_weak_hook_here"] = opening_edit_findings(hook) if hook else ""
        return saved

    def _library_payload(self, topic, run_id, run_dir, dry_run, library: Dict[str, Any]) -> Dict[str, Any]:
        """The library script as this run's script: as it is when it fits this narrator and passes the hook rule, else
        shortened to the measured word count and/or given the opening edit, and fact-checked again, as any revision
        is (never thrown away for a new one)."""
        too_long = library.pop("_too_long_here", "")
        weak_hook = library.pop("_weak_hook_here", "")
        source = (library.get("from_library") or {}).get("run_id", "another environment")
        if too_long or weak_hook:
            notes = " ".join(n for n in (too_long, weak_hook) if n)
            log.info("Library script (from %s) is edited before use (too long for this voice: %s; hook fault: %s): %s",
                     source, bool(too_long), bool(weak_hook), notes[:160])
            return self._write_and_check_script(topic, run_id, run_dir, dry_run, initial_payload=library, initial_notes=notes)
        log.info("Script taken from the script library (checked against this same fact sheet, from %s).", source)
        return library

    BED_FILES = ("ambient_bed.wav", "ambient_bed.wav.rights.json", "music_selection.json", "sound_plan.json")

    def _seed_bed_from_previous(self, run_dir: Path, vo_path: Path) -> None:
        """A regeneration whose voiceover was carried over keeps the music bed made for it too (2 min 48 s
        to make again, 2026-10-24), unless the review was about the sound. The bed's checkpoint record is
        carried with it; the stage's own check then decides whether it is still good."""
        from src.script_library import reuses
        previous = self._previous_run_dir()
        if (previous is None or not reuses("music") or (run_dir / "ambient_bed.wav").exists()
                or self._feedback().get("reason") == "sound"):
            return
        from src.checkpoints import Checkpoints
        from src.credits import sha256_file
        try:
            from src.audio_engine import voiceover_file
            same_voice = vo_path.is_file() and sha256_file(vo_path) == sha256_file(voiceover_file(previous))
            record = Checkpoints(previous).read().get("bed") or {}
        except OSError:
            return
        if not same_voice or not record.get("inputs") or not all((previous / n).is_file() for n in self.BED_FILES):
            return
        try:
            from src.audio_provenance import rebind_rights
            for n in self.BED_FILES:
                shutil.copy2(previous / n, run_dir / n)
            rebind_rights(run_dir / "ambient_bed.wav")  # before adopt: the checkpoint records the rebound file
            Checkpoints(run_dir).adopt("bed", record["inputs"], [run_dir / n for n in self.BED_FILES])
        except (OSError, ValueError) as e:
            log.warning("Could not carry the music bed over from %s: %s", previous.name, e)
            return
        log.info("Music bed carried over from %s: made for this same voiceover.", previous.name)

    def _previous_motion_dirs(self) -> List[Path]:
        from src.script_library import reuses
        previous = self._previous_run_dir()
        return [previous / "motion"] if previous and reuses("clips") and (previous / "motion").is_dir() else []

    def _seed_voice_from_previous(self, run_dir: Path, narration: str, wanted: Optional[str] = None) -> None:
        """A regeneration for pictures, sound or a cover keeps the narration: the previous run's voiceover,
        its timing, provider record and checkpoint are copied in, so the voice stage reuses it instead of
        paying for it again (a Regenerate made a new run folder and re-voiced the same words, 2026-10-24).
        Whether a copied voice may be used is still the voice stage's rule (a test voice never goes live);
        `wanted` is the provider this run would use first, and a voice from another one is not carried."""
        from src.script_library import reuses
        previous = self._previous_run_dir()
        from src.audio_engine import rights_file, voiceover_file
        if previous is None or not reuses("voice") or voiceover_file(run_dir).exists():
            return
        from src.checkpoints import Checkpoints
        try:
            meta = json.loads((previous / "script_metadata.json").read_text(encoding="utf-8"))
            record = Checkpoints(previous).read().get("voice") or {}
        except (OSError, ValueError):
            return
        take = voiceover_file(previous).name  # the WAV, or a take made before QF-08 (MP3)
        names = [take, "words.json", "voice.json"]
        if meta.get("narration_script") != narration or not record.get("inputs") or not all((previous / n).is_file() for n in names):
            return
        try:
            made_by = (json.loads((previous / "voice.json").read_text(encoding="utf-8")) or {}).get("provider")
        except (OSError, ValueError):
            made_by = None
        if wanted and made_by and made_by != wanted:
            log.info("Voiceover not carried over from %s: it was made by %s and this run uses %s.", previous.name, made_by, wanted)
            return
        for extra in (rights_file(take).name,):
            if (previous / extra).is_file():
                names.append(extra)
        try:
            from src.audio_provenance import rebind_rights
            for n in names:
                shutil.copy2(previous / n, run_dir / n)
            rebind_rights(run_dir / take)  # its rights record named the previous run's folder
            Checkpoints(run_dir).adopt("voice", record["inputs"], [run_dir / n for n in names[:3]])
        except (OSError, ValueError) as e:
            log.warning("Could not carry the voiceover over from %s: %s", previous.name, e)
            return
        log.info("Voiceover carried over from %s: the same narration, already made.", previous.name)

    def _previous_tracks(self, fb: Dict[str, Any]) -> List[str]:
        """The library files the run sent back for its music used, so its regeneration picks others."""
        if fb.get("reason") != "sound" or not fb.get("from_run"):
            return []
        try:
            chosen = json.loads((OUTPUT_DIR / fb["from_run"] / "music_selection.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [t.get("file") for t in chosen.get("tracks") or [] if t.get("file")] or ([chosen["file"]] if chosen.get("file") else [])

    def produce_deep_dive(
        self,
        topic: Dict[str, Any],
        parent_run_id: Optional[str] = None,
        auto_approve: bool = False,
        dry_run: bool = False,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Produces a 3:30 - 5:00 min 16:9 widescreen broadcast documentary according to the 5 pillars:
        - Act-by-act chunked fact verification (5 acts)
        - Sidechain audio ducking with multi-movement score
        - 16:9 aspect-aware compositing (blurred backdrops for vertical/square photos)
        - Hardware accelerated encoding (h264_nvenc)
        - Child run persistence in DB with fault isolation
        """
        tid = topic["id"]
        if not config.documentary_enabled():  # the last line of defence for any caller outside run_cycle
            log.info("Documentary for '%s' not made: %s", tid, DOCUMENTARY_PAUSED)
            return {"status": "paused", "topic_id": tid, "parent_run_id": parent_run_id,
                    "reason": DOCUMENTARY_PAUSED}
        if not run_id:
            ts = int(time.time())
            run_id = f"run_{tid}_{ts}_long"
        run_dir = OUTPUT_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        from src.publication_lock import publication_lock
        with publication_lock(self.db.db_path, "topic-images:" + tid):
            if not self.db.get_run(run_id):
                self.db.create_run(run_id, tid, parent_run_id=parent_run_id, content_format="deep_dive")
                self.db.set_setting("run_mode:" + run_id, "dry_run" if dry_run else "live")
            claim_run(self.db, run_id)  # released once its review is settled (_review_documentary)
        log.info("Starting Deep-Dive Documentary run %s for topic '%s'", run_id, topic["title"])
        # The documentary has its own progress card, like the Reel: without one it ran (and failed) unseen.
        card_title = f"{topic['title']} · 16:9 documentary"
        card = None
        try:
            card = self.telegram_bot.send_progress_card(run_id, card_title)
        except Exception:
            pass

        def step(n: int, details: str) -> None:
            if card:
                try:
                    self.telegram_bot.update_progress_card(card, n, 5, card_title, run_id, details)
                except Exception:
                    pass

        def finish(success: bool, error: str = "", alert: bool = True) -> None:
            # The documentary files its findings like a Reel (it never did, so the audit never saw it).
            self._collect_findings(run_id, run_dir, tid, error=None if success else error)
            shown = False
            if card:
                try:
                    shown = bool(self.telegram_bot.finish_progress_card(card, card_title, run_id, success=success, error=error))
                except Exception as e:
                    log.debug("Could not finish the documentary progress card: %s", e)
            if not success and not shown and alert:  # no card to turn into the failure card: say so in a message
                from src.control import short_reason
                try:
                    _notice(self, f"❌ <b>Documentary failed:</b> {html.escape(card_title)}\n\n"
                                f"{html.escape(short_reason(error, 600))}\n\nNothing was posted.", section="needs", key=f"fail:{run_id}", run=run_id,
                                 actions=notifications.failure_actions(self.db, run_id))
                except Exception:
                    pass

        try:
            self._check_cancel(run_id)
            # 1. Visual assets
            self._screen_photos(topic)
            scene_media = self.visual_engine.get_scene_sequence(topic)
            hero_img = self.visual_engine.hero_image(scene_media)
            log.info("Deep-Dive scene media: %d items.", len(scene_media))

            from src.checkpoints import Checkpoints, file_inputs, topic_inputs, cached_artifacts
            from src.config import DATA_DIR
            from src.models import DeepDivePackage
            checkpoints = Checkpoints(run_dir)
            script_inputs = {"topic": topic_inputs(topic), "research_and_rules": file_inputs([
                DATA_DIR / "fact_sheets" / f"{tid}.json", Path("src/script_engine.py"), Path("src/trace_check.py")])}
            package_path = run_dir / "verified_deep_dive.json"
            if checkpoints.reusable("deep_script", script_inputs):
                deep_dive_pkg = DeepDivePackage.model_validate_json(package_path.read_text(encoding="utf-8"))
                passed, act_issues = True, []
            else:
                # Like the Reel, a rejected draft is rewritten with the checker's findings (up to 3 drafts);
                # one shot failed Richat on a single untraced place name after 10 minutes of writing.
                feedback = ""
                for draft in range(1, 4):
                    self._check_cancel(run_id)
                    deep_dive_pkg = self.script_engine.generate_deep_dive(topic, feedback=feedback)
                    passed, act_issues = self.script_engine.verify_deep_dive_act_by_act(deep_dive_pkg, topic)
                    log.info("Deep-dive draft %d: %s", draft, "all acts trace to the fact sheet" if passed
                             else f"REJECTED ({len(act_issues)} finding(s))")
                    if passed:
                        break
                    feedback = ("The fact checker rejected these sentences; remove or reword each so it states only "
                                "what the fact sheet says:\n" + "\n".join(f"- {i}" for i in act_issues))
                if passed:
                    package_path.write_text(deep_dive_pkg.model_dump_json(indent=2), encoding="utf-8")
                    checkpoints.complete("deep_script", script_inputs, [package_path])
            if act_issues:
                log.warning("Act-by-act verification notes: %s", act_issues)

            full_script_text = deep_dive_pkg.full_narration
            raw_narration = " ".join([act.narration_text for act in deep_dive_pkg.acts])

            if not passed:
                raise ValueError("Deep-dive fact verification failed: " + "; ".join(act_issues or []))
            step(1, f"Documentary script verified ({len(deep_dive_pkg.acts)} acts)")
            # Cover copy is the fact-checked title only; the eyebrow and tagline assert nothing.
            thumbnail_copy = documentary_cover(deep_dive_pkg.title)

            payload = {
                "topic_id": tid,
                "topic_origin_note": topic_origin_note(topic),
                "title": deep_dive_pkg.title,
                "youtube_title": deep_dive_pkg.youtube_title,
                "hook": deep_dive_pkg.acts[0].narration_text[:120],
                "narration_script": raw_narration,
                "full_act_script": full_script_text,
                "caption": f"{deep_dive_pkg.title}\n\n" + deep_dive_pkg.description,
                "description": deep_dive_pkg.description,
                "tags": deep_dive_pkg.tags,
                "closing_debate_question": deep_dive_pkg.closing_debate_question,
                # The closing question only: a fixed tail ("ancient megastructures") was wrong for most topics.
                "first_comment": deep_dive_pkg.closing_debate_question,
                "location": topic.get("location") or {},
                "acts": [act.model_dump() for act in deep_dive_pkg.acts],
                "thumbnail": thumbnail_copy,
                "guardrail_passed": bool(passed),
                "verification_issues": act_issues or [],
                "origin": "deep_dive_engine",
                "delivery_profile": "deep_dive_16x9",
            }
            from src.atomic_files import write_json
            write_json(run_dir / "script_metadata.json", payload)
            self.db.update_run(run_id, "producing", script_payload=payload)

            # 3. Audio generation
            from src.audio_engine import VOICEOVER_FILE, rights_file, voiceover_file
            vo_path = voiceover_file(run_dir)
            words_path = run_dir / "words.json"
            voice_inputs = self.voice_inputs(raw_narration, dry_run)
            licensed_req, allow_paid = voice_inputs["licensed_only"], voice_inputs["allow_paid"]
            if checkpoints.reusable("deep_voice", voice_inputs):
                words = json.loads(words_path.read_text(encoding="utf-8"))
                vo_dur = self.audio_engine.get_duration(vo_path)
                log.info("Deep-Dive Voiceover: reusing existing audio (%.2fs, %d words timed)", vo_dur, len(words))
            else:
                self._check_cancel(run_id)
                vo_path = run_dir / VOICEOVER_FILE  # a new take is always the WAV (QF-08)
                words = self.audio_engine.generate_narration(raw_narration, vo_path, licensed_only=licensed_req,
                                                             allow_paid=allow_paid)
                words_path.write_text(json.dumps(words), encoding="utf-8")
                vo_dur = self.audio_engine.get_duration(vo_path)
                log.info("Deep-Dive Voiceover: %.2fs (%d words timed)", vo_dur, len(words))
                from src.audio_provenance import record_voice
                record_voice(vo_path, self.audio_engine.last_provider, self.audio_engine.last_licensed, raw_narration)
                checkpoints.complete("deep_voice", voice_inputs,
                                     [vo_path, words_path, rights_file(vo_path)])

            step(2, f"Voiceover mastered ({vo_dur:.0f}s)")
            # Compute precise chapter timestamps for description, metadata, and cinematic act-transition cues
            chapters, chapter_text, cue_timestamps = compute_act_chapters(deep_dive_pkg.acts, words, vo_dur)
            payload["chapters"] = chapters
            payload["description"] = f"{deep_dive_pkg.description}\n\n{chapter_text}"
            payload["caption"] = f"{deep_dive_pkg.title}\n\n{deep_dive_pkg.description}\n\n{chapter_text}"
            (run_dir / "script_metadata.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            self.db.update_run(run_id, "producing", script_payload=payload)

            self._check_cancel(run_id)
            bed_path = run_dir / "ambient_bed.wav"
            from src.music import load_library, MUSIC_DIR
            from src.config import MUSIC_PREFER
            library_tracks = load_library()
            from src.audio_provenance import audio_source_inputs
            # The documentary's picture cuts (documentary_cuts, as the render below plans them): its whooshes land on them
            doc_scene_cuts = caption_cut_times([c["end"] - c["start"] for c in documentary_cuts(words, vo_dur)])
            bed_inputs = {"voice_and_rules": file_inputs([vo_path, words_path, Path("src/audio_engine.py"),
                                                          Path("src/sound_plan.py"), Path("src/music.py")]),
                          "cues": cue_timestamps, "scene_cuts": doc_scene_cuts, "music_preference": MUSIC_PREFER,
                          "selected_sources": audio_source_inputs(bed_path),
                          "library": [{k: v for k, v in t.items() if k != "uses"} for t in library_tracks],
                          "library_files": file_inputs([MUSIC_DIR / t["file"] for t in library_tracks])}
            doc_music_path = run_dir / "music_selection.json"
            if not checkpoints.reusable("deep_bed", bed_inputs):
                # Background score with broadcast sidechain ducking and act-boundary risers. The sound plan comes
                # first: its dominant mood chooses the music (QF-84; never a default mood).
                from src import sound_plan as sound_plan_mod
                plan, mood_record = sound_plan_mod.plan_and_mood(words, topic.get("title", ""))
                (run_dir / "sound_plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
                music = music_for(dict(payload, topic_id=topic.get("id")), vo_dur + 0.5, run_dir,
                                  self.audio_engine.voice_quota(), is_documentary=True,
                                  mood_record=mood_record, published=self.db.music_published_uses())
                payload["music_note"] = music_note(music)
                self.db.record_music_selection(run_id, music_selection_rows(music))
                if music.get("credit"):
                    payload["music_credit"] = music["credit"]
                    payload["description"] += f"\n\n{music['credit']}"
                    payload["caption"] += f"\n\n{music['credit']}"
                    (run_dir / "script_metadata.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                self.audio_engine.generate_ambient_bed(
                    vo_dur + 0.5, bed_path, words=words,
                    music_path=music.get("path"), music_paths=music.get("paths"),
                    cue_timestamps=cue_timestamps, sound_plan=plan, scene_cuts=doc_scene_cuts
                )
                from src.audio_provenance import record_bed
                record_bed(bed_path, music, sfx=self.audio_engine.last_sfx_sources)
                # Kept with the bed, so a resumed documentary restores its music line (a held one too) (QF-84)
                doc_music_path.write_text(json.dumps(music, default=str, indent=2), encoding="utf-8")
                bed_inputs["selected_sources"] = audio_source_inputs(bed_path)
                checkpoints.complete("deep_bed", bed_inputs, [bed_path, bed_path.with_suffix(".wav.rights.json"),
                                                              doc_music_path])
            else:
                try:
                    music = json.loads(doc_music_path.read_text(encoding="utf-8"))
                    payload["music_note"] = music_note(music)
                    self.db.record_music_selection(run_id, music_selection_rows(music))
                except (OSError, ValueError) as e:   # a bed made before QF-84 kept no selection
                    log.warning("Documentary music selection unreadable (%s); its music line is not shown.", e)
                rights = json.loads(bed_path.with_suffix(".wav.rights.json").read_text(encoding="utf-8"))
                attribution = rights.get("required_attribution", "")
                if attribution and attribution not in payload["caption"]:
                    payload["caption"] += "\n\n" + attribution
                    payload["description"] += "\n\n" + attribution

            self._check_cancel(run_id)
            step(3, "Preparing 16:9 scenes, graphics & music")
            # 4. Visual preparation & compositing
            scene_media, _ = ensure_all(scene_media)
            scene_media = colour_balance.balance_all(scene_media)   # a gentle cast and exposure balance
            hero_img = prepared_still(hero_img)
            *scene_media, hero_img = privacy.anonymize_all([*scene_media, hero_img])

            # Inherit or load verified graphics data into payload
            if parent_run_id:
                parent_run = self.db.get_run(parent_run_id)
                if parent_run and parent_run.get("script_payload"):
                    p_pay = parent_run["script_payload"]
                    for k in ("stat_cards", "cross_section", "timeline", "scale_fact", "route", "photo_labels"):
                        if p_pay.get(k) and not payload.get(k):
                            payload[k] = p_pay[k]

            curated_path = CURATED_SCRIPTS_DIR / f"{tid}.json"
            if curated_path.exists():
                try:
                    c_data = json.loads(curated_path.read_text(encoding="utf-8"))
                    for k in ("stat_cards", "cross_section", "timeline", "scale_fact", "route", "photo_labels"):
                        if c_data.get(k) and not payload.get(k):
                            payload[k] = c_data[k]
                except Exception:
                    pass

            # Generate original channel graphics concurrently (map, cross-section, numbers, callouts, then/now)
            graphics_dir = run_dir / "graphics"
            graphics_dir.mkdir(parents=True, exist_ok=True)
            encoding.get_video_encoder()  # probe once before parallel renderers race to detect it under load
            t_graphics = time.perf_counter()
            payload.pop("layout_holds", None)   # QF-23: this render's holds only (a resumed run's old payload had some)
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                from src import art_direction
                doc_art = art_direction.record(run_dir, hero_img)
                payload["art_direction"] = doc_art
                # the documentary master grades its photos at bias 0 (compose_deep_dive), so do the graphics' photos
                fut_graphics = pool.submit(graphics.make_all, payload, topic, graphics_dir, photo=hero_img, width=1920, height=1080, palette=doc_art,
                                           grade_bias=0.0)
                # each glyph event names what it was drawn on (QF-94)
                from src.font_engine import on_surface
                fut_callouts = pool.submit(on_surface, "photo callouts", callouts.make, payload, topic, scene_media,
                                           graphics_dir, width=1920, height=1080, words=words,
                                           auto_approve=auto_approve, grade_bias=0.0)
                fut_then_now = pool.submit(on_surface, "graphic then/now", then_now.make, topic, graphics_dir,
                                           width=1920, height=1080, grade_bias=0.0)

                own_graphics = fut_graphics.result()
                labelled = fut_callouts.result()
                then_clip, _ = fut_then_now.result()
            StageTimer(run_dir).record("deep_graphics", time.perf_counter() - t_graphics)

            if labelled:
                own_graphics.append(labelled)
            if then_clip:
                own_graphics.append(then_clip)

            # Pictures: the topic's reviewed images (each distinct file once) and the channel's own
            # graphics, matched to the narration by the scene planner, exactly as for Reels.
            import hashlib
            unique_media: List[Path] = []
            seen_hashes = set()
            for p in scene_media:
                if p.is_file():
                    h = hashlib.sha256(p.read_bytes()).hexdigest()
                    if h not in seen_hashes:
                        seen_hashes.add(h)
                        unique_media.append(p)

            cuts = documentary_cuts(words, vo_dur)
            extended_scenes, durations, plan_notes = plan_scenes(cuts, unique_media + own_graphics, use_storyboard=False)
            payload["scene_plan_note"] = degraded_note(plan_notes)
            self._audit_documentary(vo_dur, len(deep_dive_pkg.acts), extended_scenes, durations)
            log.info("Documentary timeline: %d cuts, sum=%.2fs (target=%.2fs); %s",
                     len(extended_scenes), sum(durations), vo_dur, plan_notes[:120])

            master_mp4 = run_dir / f"{tid}_documentary_16x9.mp4"
            # Thumbnails need only the hero photo and the cover copy, which nothing below changes,
            # so they are drawn while the documentary renders (one worker: the engine keeps per-call state).
            thumb_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="deep-thumbs")
            from src.font_engine import on_surface
            fut_thumbs = thumb_pool.submit(lambda: (
                on_surface("cover", self.visual_engine.generate_thumbnails, hero_img, payload, run_dir,
                           variants=False),
                on_surface("YouTube thumbnail", self.visual_engine.generate_youtube_thumbnail, hero_img, payload,
                           run_dir / "thumbnail_youtube_16x9.jpg")))
            thumb_pool.shutdown(wait=False)
            step(4, "Rendering the documentary, preparing review")
            try:
                result = self._render_and_certify_deep_dive(
                    topic, tid, run_id, run_dir, parent_run_id, payload, words, vo_dur, vo_path, bed_path, words_path,
                    extended_scenes, durations, cuts, master_mp4, checkpoints, file_inputs, cached_artifacts,
                    fut_thumbs, auto_approve, dry_run, act_cuts=cue_timestamps)
            finally:
                concurrent.futures.wait([fut_thumbs])  # never leave a writer running past the run
            finish(result.get("status") not in ("failed", "blocked"), result.get("error", ""))  # blocked at the gate is no success
            return result

        except worker_deadline.RunDeadlineExceeded as e:  # stopped at its deadline (_stop_at_deadline), files kept
            finish(False, str(e), alert=False)  # the deadline notice already said so: one message
            return {"status": "timed_out", "run_id": run_id, "parent_run_id": parent_run_id, "topic_id": tid}
        except JobCancelled:
            finish(False, "Cancelled by operator")
            return {"status": "cancelled", "run_id": run_id, "topic_id": tid}
        except Exception as e:
            log.error("Deep-dive production failed for run %s: %s", run_id, e, exc_info=True)
            self.db.update_run(run_id, "failed", error=f"{type(e).__name__}: {e}")
            finish(False, str(e))  # the card becomes the documentary's failure card
            return {
                "run_id": run_id,
                "parent_run_id": parent_run_id,
                "topic_id": tid,
                "status": "failed",
                "error": str(e),
            }

    def _render_and_certify_deep_dive(self, topic, tid, run_id, run_dir, parent_run_id, payload, words, vo_dur,
                                      vo_path, bed_path, words_path, extended_scenes, durations, cuts, master_mp4,
                                      checkpoints, file_inputs, cached_artifacts, fut_thumbs, auto_approve, dry_run,
                                      act_cuts=None):
        timer = StageTimer(run_dir)
        t0 = time.perf_counter()
        cached_artifacts(run_dir, "caption_exports", {"words": words, "rules": file_inputs([Path("src/video_composer.py")])},
            lambda: [self.video_composer.export_subtitles_srt(words, master_mp4.with_suffix(".srt")),
                     self.video_composer.export_subtitles_vtt(words, master_mp4.with_suffix(".vtt"))])
        from src.image_presentation import policy_inputs
        render_inputs = {"presentation": policy_inputs(extended_scenes),
                         "sources": file_inputs([*extended_scenes, vo_path, bed_path, words_path,
                         Path("src/video_composer.py"), Path("src/encoded_audio.py"), Path("src/encoding.py"),
                         Path("src/still_camera.py"), Path("src/camera_motion.py")]),
                         "durations": durations, "emphasis": topic.get("key_facts", [])[:5],
                         "act_cuts": act_cuts or []}
        if checkpoints.reusable("deep_render", render_inputs):
            log.info("Master documentary video already rendered: %s", master_mp4.name)
        else:
            log.info("Composing 16:9 widescreen documentary with aspect-aware compositing...")
            self.video_composer.compose_deep_dive(
                scene_images=extended_scenes,
                vo_path=vo_path,
                bed_path=bed_path,
                words=words,
                vo_dur=vo_dur,
                output_mp4_path=master_mp4,
                durations=durations,
                emphasis=topic.get("key_facts", [])[:5],
                act_cuts=act_cuts,
            )

            checkpoints.complete("deep_render", render_inputs, [master_mp4,
                master_mp4.with_suffix(".composition.json"), master_mp4.with_name(master_mp4.stem + "_captions.ass"), master_mp4.with_suffix(".srt"),
                master_mp4.with_suffix(".vtt")])
        timer.record("deep_master_render", time.perf_counter() - t0)

        # Review proxy is tied to its current master rather than its filename.
        review_proxy = run_dir / "review_proxy.mp4"
        proxy_inputs = file_inputs([master_mp4])
        if not checkpoints.reusable("deep_proxy", proxy_inputs):
            self.video_composer.make_review_proxy(master_mp4, review_proxy)
            checkpoints.complete("deep_proxy", proxy_inputs, [review_proxy])

        # Quality audit of the master (stored under the payload's historical "self_healing" key,
        # which the release gate and existing runs read)
        timeline_cuts = []
        t_cursor = 0.0
        for c_idx, (asset, dur) in enumerate(zip(extended_scenes, durations)):
            txt = cuts[c_idx]["text"] if c_idx < len(cuts) else ""
            timeline_cuts.append({
                "scene_index": c_idx,
                "start": t_cursor,
                "end": t_cursor + dur,
                "dur": dur,
                "text": txt,
                "asset": asset,
            })
            t_cursor += dur

        t0 = time.perf_counter()
        quality_audit = QualityAuditor().certify(
            video_path=master_mp4,
            timeline=timeline_cuts,
            words=words,
            run_dir=run_dir,
            width=1920,
            height=1080,
            caption_path=master_mp4.with_name(master_mp4.stem + "_captions.ass"),
        )
        payload["self_healing"] = quality_audit
        timer.record("deep_qa", time.perf_counter() - t0)
        log.info("Documentary quality audit: score=%d/100, certified=%s",
                 quality_audit["score"], quality_audit["certified"])

        self._verify_final_copy(payload, topic)
        # Cryptographic Provenance Ledger
        prov_ledger = generate_provenance_ledger(
            run_dir=run_dir,
            topic=topic,
            payload=payload,
            master_mp4=master_mp4,
            vo_path=vo_path,
            bed_path=bed_path,
            media_items=extended_scenes,
            self_healing=quality_audit,
        )
        log.info("Recorded cryptographic provenance ledger -> %s", prov_ledger.name)
        from src.credits import add_provenance_attributions
        add_provenance_attributions(payload, prov_ledger)

        self._check_cancel(run_id)
        # Thumbnails: vertical cover, square grid, and 16:9 widescreen YouTube key-art
        (thumb_916, thumb_1x1), thumb_yt = fut_thumbs.result()
        payload["thumbnail_youtube"] = str(thumb_yt)
        (run_dir / "script_metadata.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        self.db.update_run(
            run_id,
            "pending_approval",
            master_video_path=master_mp4,
            script_payload=payload,
            thumbnail_cover_path=thumb_yt,
            thumbnail_grid_path=thumb_1x1,
            parent_run_id=parent_run_id,
            content_format="deep_dive",
        )

        from src.release import ReleaseReportLocked, locked_report, write_release_report
        try:
            release = write_release_report(self.db.get_run(run_id), dry_run=dry_run)
        except ReleaseReportLocked as e:   # held, with the reason on the card; the card re-runs the gate
            log.warning("Release report not saved: %s", e)
            release = locked_report(e)
        auto_approve = self._auto_approve_allowed(run_id, payload, auto_approve, scenes=extended_scenes)
        blocked = self._blocked_at_gate(run_id, release, dry_run, "auto-approve flag") if auto_approve else None
        if blocked:
            return {"run_id": run_id, "parent_run_id": parent_run_id, "topic_id": tid, "status": "blocked",
                    "error": blocked, "master_video": str(master_mp4), "format": "deep_dive_16x9"}
        if auto_approve:
            self.db.record_approval(run_id, tid)
            self.db.update_approval_decision(run_id, "approved", decided_by="auto-approve-flag")
            self.db.update_run(run_id, "simulated" if dry_run else "approved")

        return {
            "run_id": run_id,
            "parent_run_id": parent_run_id,
            "topic_id": tid,
            # Only an auto-approved documentary is decided here; a dry run still goes to review first
            # (a dry run reported "simulated" and skipped the review card, as the Richat test showed).
            "status": ("simulated" if dry_run else "approved") if auto_approve else "pending_approval",
            "master_video": str(master_mp4),
            "thumbnail_youtube": str(thumb_yt),
            "duration_seconds": vo_dur,
            "format": "deep_dive_16x9",
        }

    def _review_documentary(self, produced: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
        """Sends a rendered documentary for review and acts on the decision (approve publishes it to YouTube,
        or simulates that in dry run). An auto-approved one is already decided."""
        if produced.get("status") != "pending_approval":
            if produced.get("run_id"):
                release_run(self.db, produced["run_id"])
            return produced
        if dry_run:
            self.db.set_setting("run_mode:" + produced["run_id"], "dry_run")
        try:
            return {**produced, **self.request_approval(produced["run_id"], dry_run=dry_run)}
        finally:
            release_run(self.db, produced["run_id"])

    def _alert(self, text: str, buttons: Optional[list] = None, group: Optional[str] = None, **item: Any) -> None:
        """A notice for the operator. In the clean chat it is an item of the pinned panel: `item` names its
        section ("needs" or "info"), and where it coalesces its key, run, replace and its actions (UI-01, design
        §10.2)."""
        notifications.say(self.telegram_bot.notify, text, buttons=buttons, group=group or "studio", **item)

    def _episode_action(self, run_id: str) -> list[dict[str, str]]:
        """🗓️ Posting for a notice about one episode's posts: its page on Posting (design §10.2)."""
        return [{"text": "🗓️ Posting", "callback_data": f"cpost:{self.db.run_key(run_id)}"}]

    def confirm_posted(self, run_id: str) -> str:
        """Human confirmed an 'unknown' publish did go live: count it as published."""
        run = self.db.get_run(run_id)
        if not run or run["status"] != "publish_unknown":
            raise ValueError("That Reel isn't waiting for a posted/not-posted answer.")
        self.db.update_run(run_id, "published")
        self.db.record_publication(run["topic_id"], run_id, "manual-confirmation", "published", {"confirmed_by": "human"})
        self.topic_engine.record_status(run["topic_id"], "published", run_id=run_id)
        return f"✅ Recorded as published: {html.escape(self.db.run_label(run_id))}"

    def confirm_not_posted(self, run_id: str) -> str:
        """Human confirmed an 'unknown' publish did not go live: requeue the topic for a retry."""
        run = self.db.get_run(run_id)
        if not run or run["status"] != "publish_unknown":
            raise ValueError("That Reel isn't waiting for a posted/not-posted answer.")
        self.db.update_run(run_id, "failed", error="Confirmed by a human: not posted")
        self.topic_engine.record_status(run["topic_id"], "queued", run_id=run_id)
        return f"🔁 Not posted, so the topic is back in the queue: {html.escape(self.db.run_label(run_id))}"

    def simulate_publish(self, run_id: str) -> str:
        """Execute a simulated publish for a rendered run without broadcasting live to Meta."""
        self.publish_run(run_id, dry_run=True)
        return f"🧪 <b>Simulated Publish Complete</b> for {html.escape(self.db.run_label(run_id))}. Nothing was posted."

    def _approval_window(self) -> int:
        """How long a review card stays open: with scheduled posting, until 20 minutes before the next
        free Reel slot (an approval then still makes that slot), between the configured timeout and 24 h."""
        from src.config import APPROVAL_TIMEOUT_SECONDS
        if not POST_SCHEDULING:
            return APPROVAL_TIMEOUT_SECONDS
        now = datetime.now(timezone.utc)
        slot = post_schedule.next_reel_slot(now, self._booked_reel_days())
        until_slot = (slot - now).total_seconds() - 20 * 60
        # ...and never into the next daily run, which needs the production lock this wait holds.
        from zoneinfo import ZoneInfo
        hour, minute = (int(x) for x in DAILY_RUN_TIME.split(":"))
        local = now.astimezone(ZoneInfo(TIMEZONE))
        next_daily = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_daily <= local:
            next_daily += timedelta(days=1)
        until_daily = (next_daily - local).total_seconds() - 10 * 60
        return int(max(APPROVAL_TIMEOUT_SECONDS, min(until_slot, until_daily, 24 * 3600)))

    EDITABLE_FIELDS = ("caption", "title", "first_comment", "carousel_caption")

    def edit_copy(self, run_id: str, field, text: str = "") -> str:
        """✏️ Edit text on a review card: the new wording is fact-checked like the rest of the public copy;
        if it passes, the payload, metadata and release report are updated and a fresh card replaces the old
        one. Credit lines the caption must carry (music, CC BY images) are kept by the release gate check.
        `field` is one field name (with `text`) or {field: text} for several, checked and re-sent together."""
        changes = dict(field) if isinstance(field, dict) else {field: text}
        run = self.db.get_run(run_id)
        appr = self.db.get_approval(run_id) or {}
        if not run or appr.get("decision") != "pending":
            return "That run is no longer waiting for review."
        changes = {f: v.strip() for f, v in changes.items() if f in self.EDITABLE_FIELDS and (v or "").strip()}
        if not changes:
            return "Nothing to change."
        if len(changes.get("title", "")) > 60:
            return f"Titles are at most 60 characters (this one is {len(changes['title'])})."
        payload = dict(run["script_payload"])
        old = {f: payload.get(f, "") for f in changes}
        from src.credits import keep_attribution, keep_hashtags
        for f in ("caption", "carousel_caption"):  # hashtags (unless replaced) and credits stay
            if f in changes:
                changes[f] = keep_attribution(old[f], keep_hashtags(old[f], changes[f]))
        # The edit card's template carries every field: what comes back unchanged is not an edit.
        def same(a, b):  # spacing and blank lines are not edits
            return " ".join((a or "").split()) == " ".join((b or "").split())
        changes = {f: v for f, v in changes.items() if not same(v, old[f])}
        if not changes:
            return "Nothing changed: the text you sent matches the current copy."
        old = {f: old[f] for f in changes}
        payload.update(changes)
        # Credit lines written before QF-42 are rewritten with any edit (caption, description, carousel caption, first
        # comment), so a run the gate's credit_lines check holds for an old line can be fixed here.
        from src.credits import rewrite_payload_credits
        rewrite_payload_credits(payload)
        labels = [f.replace("_", " ") for f in changes]  # "title, carousel caption and caption"
        names = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]
        topic = self.topic_engine.get_topic_by_id(run["topic_id"]) or {"id": run["topic_id"]}
        try:
            self._verify_final_copy(payload, topic)
        except ValueError as e:
            return f"❌ Not changed: the new {names} failed the fact check.\n<i>{html.escape(str(e)[:500])}</i>"
        if not self.db.commit_review_copy(run_id, appr.get("revision", 0), payload):
            return "Review changed while checking the edit. Nothing was changed; open the current review."
        run_dir = Path(run["master_video_path"]).parent
        from src.atomic_files import write_json
        write_json(run_dir / "script_metadata.json", payload)
        from src.release import ReleaseReportLocked, locked_report, write_release_report
        try:
            report = write_release_report(self.db.get_run(run_id))
        except ReleaseReportLocked as e:   # the edit stands; the card's own re-check saves the report later
            log.warning("Release report not saved after the edit: %s", e)
            report = locked_report(e)
        if appr.get("telegram_message_id"):
            self.telegram_bot._strip_buttons(appr["telegram_message_id"])
        # The review copy, as every re-send uses: the master (78 MB) was over Telegram's upload limit, so the
        # edited card failed with "A deliverable review video is required" (dev, 2026-10-23).
        proxy = run_dir / "review_proxy.mp4"
        video = proxy if proxy.is_file() else Path(run["master_video_path"])
        self.telegram_bot.send_approval_request(video, Path(run["thumbnail_cover_path"] or run_dir / "thumbnail_cover.jpg"), payload, run_id)
        for f in changes:
            log.info("Edited %s of %s: %r -> %r", f, run_id, old[f][:60], changes[f][:60])
        open_checks = [c["name"] for c in report.get("checks", []) if c.get("status") != "pass"]
        # The card is sent first, so it sits above this confirmation (it said "below": operator, 2026-10-23).
        return (f"✅ {names[:1].upper() + names[1:]} updated and fact-checked; the updated review card is just above."
                + (f" Release gate still open: {', '.join(open_checks)}." if open_checks else ""))

    def request_approval(self, run_id: str, dry_run: bool = False, resend: bool = True) -> Dict[str, Any]:
        """Re-sends the review card for a rendered run (resend=False: the card is already in the chat, as
        after a restart), then acts on the decision. A Regenerate is returned with the run's topic: the
        caller starts the regeneration run in its own worker (start_regeneration)."""
        run = self.db.get_run(run_id)
        if not run or run["status"] not in ("timeout", "pending_approval"):
            raise ValueError(f"Run status '{run and run['status']}' has no approval to re-send.")
        dry_run = dry_run or self.db.get_setting("run_mode:" + run_id) == "dry_run"
        appr = self.db.get_approval(run_id)
        if resend and appr and appr["decision"] == "approved":  # never a fresh card with Approve on an approved run
            raise ValueError("That review was already approved; it is being posted or booked.")
        if appr and appr["decision"] == "timeout":
            self.db.reopen_approval(run_id)
        elif not appr:
            self.db.record_approval(run_id, run["topic_id"])
        self.db.update_run(run_id, "pending_approval")
        if resend:
            run_dir = OUTPUT_DIR / run_id
            proxy = run_dir / "review_proxy.mp4"
            video = proxy if proxy.exists() else Path(run["master_video_path"])
            self.telegram_bot.send_approval_request(video, Path(run["thumbnail_cover_path"]), run["script_payload"], run_id)
        decision, acted = self._decide_review(run_id, lambda d: self._after_approval(run_id, dry_run=dry_run)
                                              if d == "approved" else self._settle_decision(run_id, run["topic_id"], d))
        if decision is None:  # another wait already owns this review: the card was only brought down
            return {"run_id": run_id, "decision": "pending", "already_waiting": True, "topic_id": run["topic_id"]}
        if decision == "approved":
            return {"run_id": run_id, "decision": decision, **(acted or {"already_acted": True})}
        return {"run_id": run_id, "decision": decision, "status": decision, "topic_id": run["topic_id"]}

    REVIEW_ACTED_PREFIX = "review-acted:"

    def _decide_review(self, run_id: str, act, decided: Optional[str] = None) -> Tuple[Optional[str], Any]:
        """The one wait on a review's decision, and acting on it, across every process: a Resend (the Reviews
        list, the reminder's "Bring the card down") while the first wait ran started a second wait, and one
        Approve was acted on twice: two Reel bookings, the Stories and the carousel posted twice (reviews of
        2026-10-25). The wait and `act(decision)` both run under the run's review lock; an Approve already acted
        on (its decision time is recorded first) is never acted on again by a later wait. Returns (None, None)
        when another wait owns the review, (decision, None) when this Approve was already acted on.
        `decided` is a decision already made (the auto-approve flag): it is acted on under the same lock and
        marker, without a wait, so a review wait resumed elsewhere cannot act on it again (dev, 2026-10-29)."""
        db_path = getattr(self.db, "db_path", None)
        if db_path is None:
            decision = decided or self.telegram_bot.wait_for_decision(run_id, self._approval_window())
            return decision, act(decision)
        from src.publication_lock import exclusive
        with exclusive(db_path, "review-wait:" + run_id) as mine:
            if not mine:
                log.info("A wait on %s's review is already running; it acts on the decision, not this one.", run_id)
                return None, None
            while True:
                if decided:
                    decision = decided
                else:
                    self.telegram_bot.approval_window = self._approval_window   # a resend's window is sized again from it
                    decision = self.telegram_bot.wait_for_decision(run_id, self._approval_window())
                if decided or decision not in UNDOABLE or not undone_before_acting(
                        self.db, run_id, decision, getattr(self, "telegram_bot", None)):
                    break
            if decision != "approved":
                return decision, act(decision)
            stamp = str((self.db.get_approval(run_id) or {}).get("decision_timestamp") or "")
            key = self.REVIEW_ACTED_PREFIX + run_id
            if stamp and self.db.get_setting(key) == stamp:
                run = self.db.get_run(run_id) or {}
                booked = self.db.list_scheduled_posts(("pending", "waiting", "published", "unknown", "failed"), run_id=run_id)
                if run.get("status") != "pending_approval" or booked:
                    log.info("The Approve of %s was already acted on; not again.", run_id)
                    return decision, None
                # Marked, yet nothing happened (the process ended part-way): booking and publishing are
                # idempotent, so it is acted on again rather than left approved and never posted.
                log.warning("The Approve of %s was marked acted on but nothing was posted or booked; acting now.", run_id)
            self.db.set_setting(key, stamp)  # before acting: two waits never act on one Approve
            try:
                return decision, act(decision)
            except Exception as e:
                if self._refused_for_good(e):
                    # The gate (or the run itself) refuses it: settled as failed, with its reason, once. Left
                    # waiting, the sweep tried it again every two minutes, an alert each time (dev, 2026-10-26).
                    run = self.db.get_run(run_id) or {}
                    self.db.update_run(run_id, "failed", error=f"Approved, then refused: {e}")
                    if run.get("topic_id"):
                        self.topic_engine.record_status(run["topic_id"], "failed", run_id=run_id)
                else:
                    self.db.set_setting(key, "")  # a passing failure: a later wait (or a resend) acts on it again
                raise

    def _auto_approve_allowed(self, run_id: str, payload: dict[str, Any] | None, auto_approve: bool,
                              scenes: list[Any] | tuple[Any, ...] = ()) -> bool:
        """Whether the auto-approve flag may decide this run: never when it shows an AI illustration (operator,
        2026-11-02: an illustration must reliably depict its subject, and the operator approves each one on the review
        card), read from payload["picture_fit"] and from the rendered `scenes` themselves (the deep dive records no
        picture_fit). A refused flag sends the run to review instead, with the reason logged and alerted."""
        if not auto_approve:
            return False
        from src.image_gen import auto_approve_refusal
        why = auto_approve_refusal(payload, scenes)
        if not why:
            return True
        log.warning("%s: %s; sent for review instead.", run_id, why)
        try:
            _notice(self, f"🏛 <b>Auto-approve refused</b>\n\n<b>{html.escape(self.db.run_label(run_id))}</b>\n"
                        + html.escape(why) + "\nThe review card lists each illustration with its reference(s).", section="needs", run=run_id,
                         actions=[{"text": "🎬 Reviews", "callback_data": "creviews:"}])
        except Exception as e:  # noqa: BLE001 - the refusal stands whether or not the alert is delivered
            log.warning("Auto-approve refusal alert not sent (%s).", e)
        return False

    def _blocked_at_gate(self, run_id: str, report: Optional[Dict[str, Any]], dry_run: bool, by: str) -> Optional[str]:
        """An approval that does not come from the Telegram card (the auto-approve flag, the CLI) meets the
        same release gate first: the card offers no Approve on a blocked version (send_review_card), and these
        paths approved it anyway, to be refused at release (dev, 2026-10-27: Trepanation approved seven times,
        Odessa and Roman approved by the flag 0-11 s after their reports failed). A blocked run is recorded as
        blocked at the gate, never approved; returns why, or None when the gate passes."""
        from src.release import release_problems
        problems = release_problems(report, dry_run)
        if not problems:
            return None
        why = f"Blocked at the release gate, not approved ({by}): " + "; ".join(problems)
        run = self.db.get_run(run_id) or {}
        self.db.update_run(run_id, "failed", error=why[:2000])
        if run.get("topic_id"):
            self.topic_engine.record_status(run["topic_id"], "failed", run_id=run_id)
        log.warning("%s: %s", run_id, why)
        _notice(self, f"⛔ <b>Blocked at the release gate</b>, not approved ({html.escape(by)})\n\n"
                    f"<b>{html.escape(self.db.run_label(run_id))}</b>\n<blockquote expandable>"
                    + html.escape("\n".join(problems)[:900]) + "</blockquote>\nNothing was posted.", section="needs", key=f"gate:{run_id}", run=run_id,
                     actions=[{"text": "Details", "callback_data": f"chist:{self.db.run_key(run_id)}"}])
        return why

    @staticmethod
    def _refused_for_good(error: Exception) -> bool:
        """An approval that acting again cannot change: the release gate blocks this version, it has no video,
        or its curated script was never reviewed. A busy lock or a network error passes and is retried."""
        text = str(error)
        return isinstance(error, ValueError) and (
            text.startswith("Release blocked") or "has no rendered video" in text or "unreviewed curated script" in text)

    def _wait_for_review(self, run_id: str) -> Optional[str]:
        """The decision alone (callers that act themselves); see _decide_review."""
        return self._decide_review(run_id, lambda d: None)[0]

    def _settle_decision(self, run_id: str, topic_id: str, decision: str) -> None:
        """A review decided anything but Approve: the run's and the topic's state, the same whichever wait
        saw it (the run's own, a Resend's, or one resumed after a restart). The two paths had drifted:
        a timeout failed the topic in one and left it in production in the other, and one of them alerted
        a second time after the wait's own "no decision" notice (2026-10-23 audit)."""
        run_status, topic_status = {
            "rejected": ("rejected", "rejected"),
            "regenerate": ("regenerate", None),   # the regeneration run takes the topic on
            "timeout": ("timeout", "failed"),     # the render is kept for Resend; the topic may be retried
        }.get(decision, ("failed", "failed"))
        self.db.update_run(run_id, run_status)
        if topic_status:
            self.topic_engine.record_status(topic_id, topic_status, run_id=run_id)
        log.info("Not publishing %s: decision was '%s'.", run_id, decision)

    def resume_mode(self, run_id: str, dry_run: bool) -> bool:
        return resume_mode(self.db, run_id, dry_run)

    def resume_run(self, run_id: str, auto_approve: bool = False, dry_run: bool = False,
                   require_live: bool = False) -> Dict[str, Any]:
        """
        Continues a failed run from where it stopped, reusing its approved script and
        voiceover, so a late failure (e.g. rendering) doesn't mean paying for them again.
        """
        run = self.db.get_run(run_id)
        if not run:
            raise ValueError(f"Unknown run '{run_id}'.")
        if run["status"] not in ("failed", "timed_out", "producing"):
            raise ValueError(f"Run status '{run['status']}' can't be resumed.")
        run_dir = OUTPUT_DIR / run_id
        if not run_dir.is_dir():
            raise ValueError(f"Run folder {run_dir} no longer exists.")
        dry_run = resume_mode(self.db, run_id, dry_run)
        topic = self.topic_engine.get_topic_by_id(run["topic_id"])
        previous_status = "published" if topic["times_published"] else "queued"
        self.db.update_run(run_id, "producing")
        claim_run(self.db, run_id)
        self.db.reset_approval(run_id)  # the new version waits for its own decision
        self.topic_engine.record_status(topic["id"], "in_production", run_id=run_id)
        log.info("Resuming run %s", run_id)
        from src.ops import RunLog
        sink = RunLog(run_dir).start()
        try:
            result = self._produce_and_publish(topic, run_id, run_dir, auto_approve, dry_run, previous_status)
            # As run_cycle does: the progress card ends and the run's findings (the self-audit among them) are
            # filed. A resumed run skipped both, so its cards were never checked (2026-10-24).
            self._finish_progress(topic.get("title", run_id), run_id, success=True)
            self._collect_findings(run_id, run_dir, topic["id"])
        except worker_deadline.RunDeadlineExceeded as e:  # stopped at its deadline (_stop_at_deadline), files kept
            self._collect_findings(run_id, run_dir, topic["id"], error=f"RunDeadlineExceeded: {e}")
            return {"status": "timed_out", "run_id": run_id, "topic_id": topic["id"]}
        except Exception as e:
            self.db.update_run(run_id, "failed", error=f"{type(e).__name__}: {e}")
            self.topic_engine.record_status(topic["id"], "failed", run_id=run_id)
            log.error("Run %s failed: %s", run_id, e)
            card_shown = False
            if getattr(self, "progress_msg_id", None):  # as in run_cycle: the progress card becomes the failure card
                try:
                    card_shown = bool(self.telegram_bot.finish_progress_card(self.progress_msg_id, topic["title"], run_id,
                                                                             success=False, error=str(e)))
                except Exception:
                    pass
                self.progress_msg_id = None
            if not card_shown:
                _notice(self, f"❌ <b>{failed_heading(self.db, run_id)}</b>\n\n<b>{html.escape(self.db.run_label(run_id))}</b>\n<blockquote expandable>{html.escape(str(e)[:600])}</blockquote>\nNothing was posted. The topic is set aside: restore it from the queue (/queue requeue N).", section="needs", key=f"fail:{run_id}", run=run_id,
                             actions=notifications.failure_actions(self.db, run_id))
            ping_healthcheck("/fail")
            raise
        finally:
            sink.stop()
            release_run(self.db, run_id)
        if result.get("status") == "regenerate":
            # Regenerate on a re-rendered run's review starts the new version, as it does after a first render
            # (production 2026-10-25: it was recorded and nothing followed).
            return self.regenerate_from(run_id, auto_approve=auto_approve, dry_run=dry_run, require_live=require_live)
        return result

    def regenerate_from(self, run_id: str, auto_approve: bool = False, dry_run: bool = False,
                        require_live: bool = False) -> Dict[str, Any]:
        """A new version of a run sent back with Regenerate, guided by the reason given on its card. The live
        preflight travels with it (a regeneration after a resume ran without it: review, 2026-10-25)."""
        run = self.db.get_run(run_id) or {}
        log.info("Regeneration requested; starting a fresh run for '%s'.", run.get("topic_id"))
        self._operator_feedback = self._await_reason(run_id)
        try:
            return self.run_cycle(run["topic_id"], auto_approve, dry_run, require_live, run.get("content_format") or "reel", 1)
        finally:
            self._operator_feedback = None

    def _upright_photos(self, topic: Dict[str, Any]) -> set:
        """Photos stored turned over (fetched before 2026-10-25) are fetched again upright; returns those that could
        not be, which the run leaves out (src/photo_orientation.py). Never fails a run."""
        try:
            from src.photo_orientation import put_upright
            done, still = put_upright(self.visual_engine.assets_dir / topic["id"])
        except Exception as e:
            log.warning("Photo orientation check for %s did not run: %s", topic.get("id"), e)
            return set()
        if done:
            _notice(self, f"🔄 <b>Photos turned over</b> in {html.escape(topic.get('title') or topic['id'])}:\n"
                        + "\n".join("• " + html.escape(x) for x in done), section="info")
        return still

    def _screen_photos(self, topic: Dict[str, Any]) -> None:
        """Vision verdicts for the topic's photos that have none (src/image_review.py). Never fails a run:
        a photo left unscreened is simply not used."""
        try:
            from src.image_review import screen_unchecked
            screen_unchecked(topic, self.visual_engine.assets_dir / topic["id"])
        except Exception as e:
            log.warning("Photo screening for %s did not run: %s", topic.get("id"), e)

    def rerender_refusal(self, run_id: str) -> Optional[str]:
        """Why a failed run can't be re-rendered in place, or None. A failed run qualifies once its script has
        passed the fact check (src/resume_points.py): everything it completed after that is reused where its
        inputs are unchanged. A rejected run is regenerated with the reason given (its own rule, unchanged)."""
        from src.resume_points import worth_resuming
        run = self.db.get_run(run_id)
        if not run:
            return "That run is no longer recorded."
        if run["status"] not in REMAKE_STATUSES:
            return f"That run is {run['status'].replace('_', ' ')}, not failed or rejected."
        if not (OUTPUT_DIR / run_id).is_dir():
            return "That run's files are no longer kept; Render the topic again instead."
        if run["status"] in ("rejected", "regenerate"):  # regenerate: sent back, and its new version never began
            return None if run.get("master_video_path") else "That run stopped before it had a video; Render the topic again instead."
        if not worth_resuming(OUTPUT_DIR / run_id, run):
            return "That run stopped before its script passed the fact check, so nothing it made is worth keeping; Render the topic again instead."
        return None

    def remake_args(self, run_id: str) -> List[str]:
        """How a run is made again: a failed one is resumed in place; a rejected one is regenerated with the
        reason you gave (a new run that carries the old one's voiceover and clips over)."""
        run = self.db.get_run(run_id) or {}
        if run.get("status") not in ("rejected", "regenerate"):
            return []
        return ["--regenerate-from", run_id, "--topic-id", str(run.get("topic_id") or "")]

    # ------------------------------------------------------------ posting a dry-run result for real
    def post_now(self, run_id: str) -> str:
        """Post for real at once (operator, 2026-10-24): the Reel now, Stories and carousel booked after it.
        For a run already booked into a slot, the booking is posted now and its Stories re-timed after it."""
        run = self.db.get_run(run_id) or {}
        if run.get("status") != "scheduled":
            return self.promote_run(run_id, now=True)
        booking = next((p for p in self.db.list_scheduled_posts(("pending", "blocked"), run_id=run_id) if p["kind"] == "reel"), None)
        if booking is None:
            return "⚡ No pending Reel booking for that run."
        from src.release import ReleaseBlocked
        try:   # the checks: nothing is uploaded yet, so any failure here means it was not posted
            self._require_release(run, dry_run=False, retry_now=True)
        except ValueError as e:
            return self._blocked_reply("⚡", run_id, e)
        except Exception as e:  # noqa: BLE001 - the checker or network: nothing was posted, the booking stands
            return f"⚡ Not posted: it could not be checked just now ({html.escape(_error_words(e))}). Try again later."
        try:   # the upload: from its claim on, a failure means the outcome is unknown, never "not posted"
            res = self.publish_run(run_id, dry_run=False, previous_status="published" if run.get("times_published") else "queued")
        except ReleaseBlocked as e:
            return self._blocked_reply("⚡", run_id, e)
        except Exception as e:  # noqa: BLE001
            if (self.db.get_run(run_id) or {}).get("status") != "publish_unknown":
                return f"⚡ Not posted: {html.escape(_error_words(e))}"
            self._unknown_booking(booking, e)
            return ("⚡ <b>Outcome unknown</b>: the upload started and no answer came "
                    f"({html.escape(_error_words(e))}). Check Facebook and Instagram before trying again, so nothing "
                    "posts twice; then answer It posted or It didn't post.")
        now = datetime.now(timezone.utc)
        self.db.update_scheduled_post(booking["id"], res["status"], detail={
            "error": res.get("publish_result", {}).get("error"), "posted_at": now.isoformat()})
        if res.get("status") != "published":
            return f"⚡ Publishing ended with status '{html.escape(str(res.get('status')))}'; see the receipt above."
        for story in self.db.list_scheduled_posts(("pending",), run_id=run_id):
            if story["kind"] == "story":
                self.db.update_scheduled_post(story["id"], "pending",
                                              due_at=post_schedule.story_time(now).astimezone(timezone.utc).isoformat())
        # Its carousel's slot counts from now, when the Reel really posted (post_schedule.carousel_reel): the
        # booking's confirmation named one counted from the Reel's slot (operator, 2026-11-03)
        carousels = [p for p in self.db.list_scheduled_posts(("waiting",), run_id=run_id) if p["kind"] == "carousel"]
        car = (post_schedule.first_carousel_slot(post_schedule.carousel_reel(self.db, carousels[-1])[1], now=now)
               if carousels else None)
        then = (f", the carousel {html.escape(post_schedule.fmt(car))}" if car else
                ", its carousel has no carousel slot left, so it won't post" if carousels else "")
        return (f"⚡ <b>{html.escape(self.db.run_label(run_id))}</b> is posted; its Stories follow in "
                f"{config.STORY_DELAY_MINUTES} minutes{then}.")

    def _blocked_reply(self, icon: str, run_id: str, exc: Exception, verb: str = "posted") -> str:
        """A release refusal in plain words, with what can work (QF-110): trying again re-runs the same checks."""
        from src.posting_view import BLOCKED_NEXT
        reasons = self._block_reasons(run_id, exc)
        from src.release import ReleaseBlocked
        return (f"{icon} Not {verb}: " + "; ".join(html.escape(r) for r in reasons)
                + ("\n\n" + BLOCKED_NEXT if isinstance(exc, ReleaseBlocked) else ""))

    def prepare_new_version(self, run_id: str) -> str | None:
        """For "Make a new version" on a blocked booking (QF-110): its bookings are cancelled and the run is sent
        back for a new version (status regenerate: Re-render makes it from this one, reusing what was paid for, and
        it comes to review). Returns why not, or None. Every refusal comes before anything is changed: never for a
        run whose Reel may be live (being uploaded, posted, or its outcome unknown: It posted / It didn't post
        decides that), nor while a run is being made."""
        run = self.db.get_run(run_id) or {}
        posts = self.db.list_scheduled_posts(("pending", "waiting", "blocked"), run_id=run_id)
        blocked = [p for p in posts if p["kind"] == "reel" and p["status"] == "blocked"]
        if not blocked:
            return "That episode has no blocked booking."
        if run.get("status") not in ("scheduled", "approved", "simulated") or any(
                x.get("status") in ("published", "unknown") for x in self.db.list_publications(run_id)):
            return ("Its Reel may already be live (it was uploaded, or its outcome is unknown), so no new version is "
                    "made: check the platforms first.")
        if self.db.get_active_progress() is not None:
            return "A run is being made now; make the new version when it has finished."
        for p in posts:
            self.db.update_scheduled_post(p["id"], "cancelled", detail={"reason": "replaced by a new version"})
        self.db.set_setting(PROMOTED_PREFIX + run_id, "")
        # The reason the regeneration acts on, recorded now, so it never waits for a reason chip that is not coming
        reasons = (blocked[0].get("detail") or {}).get("reasons") or []
        facts = any(str(r).startswith("Fact check") for r in reasons)
        self.db.set_setting(f"decision_reason:{run_id}", json.dumps({
            "reason": "facts" if facts else "other", "decision": "regenerate",
            "text": "" if facts else "Blocked at its posting slot: " + "; ".join(map(str, reasons))[:500]}))
        self.db.update_run(run_id, "regenerate")
        return None

    def cancel_post(self, post_id: int) -> str:
        """Cancels a booking (operator, 2026-10-24). A Reel's Stories and carousel go with it, and the run
        is a post candidate again; a Stories or carousel booking goes alone."""
        post = next((p for p in self.db.list_scheduled_posts(("pending", "waiting", "blocked")) if p["id"] == int(post_id)), None)
        if post is None:
            return "⏹ That booking is no longer pending."
        run_id = post["run_id"]
        if post["kind"] != "reel":
            self.db.update_scheduled_post(post["id"], "cancelled", detail={"reason": "cancelled by the operator"})
            return f"⏹ {post['kind'].capitalize()} booking cancelled for {html.escape(self.db.run_label(run_id))}."
        for other in self.db.list_scheduled_posts(("pending", "waiting", "blocked"), run_id=run_id):
            self.db.update_scheduled_post(other["id"], "cancelled", detail={"reason": "cancelled by the operator"})
        self.db.set_setting(PROMOTED_PREFIX + run_id, "")
        run = self.db.get_run(run_id) or {}
        if run.get("status") == "scheduled":
            self.db.update_run(run_id, "simulated")
            self.topic_engine.record_status(run.get("topic_id"), "queued", run_id=run_id)
        return f"⏹ Booking cancelled: {html.escape(self.db.run_label(run_id))} is a post candidate again."

    def move_post(self, post_id: int) -> str:
        """Moves a Reel booking to the next free slot after its own; its Stories move with it."""
        post = next((p for p in self.db.list_scheduled_posts(("pending",)) if p["id"] == int(post_id) and p["kind"] == "reel"), None)
        if post is None:
            return "⏭ That Reel booking is no longer pending."
        after = datetime.fromisoformat(post["due_at"]) + timedelta(minutes=1)
        slot = self._reel_slot(post["run_id"], after, self._booked_reel_days(exclude_id=post["id"]))
        self.db.update_scheduled_post(post["id"], "pending", due_at=slot.astimezone(timezone.utc).isoformat())
        for story in self.db.list_scheduled_posts(("pending",), run_id=post["run_id"]):
            if story["kind"] == "story":
                self.db.update_scheduled_post(story["id"], "pending",
                                              due_at=post_schedule.story_time(slot).astimezone(timezone.utc).isoformat())
        return f"⏭ {html.escape(self.db.run_label(post['run_id']))} moved to {html.escape(post_schedule.fmt(slot))}."

    def retry_post(self, post_id: int) -> str:
        """Re-sends a partially published Stories frame or carousel to the platform(s) that failed, under a
        fresh idempotency key, so the platform that posted is never sent twice (operator, 2026-10-24: frame 3
        of the first Stories went to Facebook but not Instagram, and the only choice was to leave it)."""
        post = next((p for p in self.db.list_scheduled_posts(("unknown", "failed")) if p["id"] == int(post_id)), None)
        if post is None or post["kind"] not in ("story", "carousel"):
            return "🔁 Nothing to retry for that booking."
        detail = dict(post.get("detail") or {})
        failed = dict(detail.get("failed") or {})
        if not failed:  # an older record: the platforms named in the error text
            m = re.search(r"failed=\[([^\]]*)\]", str(detail.get("error") or ""))
            failed = {p.strip(" '\"") : "" for p in m.group(1).split(",") if p.strip(" '\"")} if m else {}
        if not failed:
            return "🔁 That booking's failed platform is not recorded; nothing was retried."
        # Never a blind repeat (operator, 2026-10-24): a retry whose outcome is unknown may have posted, so the
        # platform is checked by hand before another; and two retries are the most a booking gets.
        last = detail.get("last_retry") or {}
        if last.get("status") == "unknown":
            return ("🔁 The last retry's outcome is unknown (the upload started, the answer never came): check the "
                    "platform before retrying, so nothing posts twice.")
        if int(detail.get("retries") or 0) >= 2:
            return "🔁 This booking has been retried twice; it is not retried again automatically."
        files = [f for f in post["files"] if Path(f).exists()]
        part = int(detail.get("part") or (int(detail.get("posted_parts") or 0) + 1))
        if post["kind"] == "story":
            from src.multi_format import STORY_FRAMES, story_posting_frames
            if part > len(STORY_FRAMES):
                return "🔁 Not retried: that was the poll frame, which is no longer posted."
            # The frame by its number in the sequence (QF-80), not its place among the files still on disk.
            frames = [f for n, f in story_posting_frames(files) if n == part]
        else:
            frames = files
        if not frames:
            return "🔁 The frame to retry is no longer on disk."
        run = self.db.get_run(post["run_id"]) or {}
        try:
            caption, comment = (self._carousel_request(post["run_id"], run.get("script_payload") or {}, frames)
                                if post["kind"] == "carousel" else ("", ""))
        except ValueError as e:   # its caption is not the one the review card showed (QF-43)
            return f"🔁 Not retried: {html.escape(str(e))}"
        attempt = int(detail.get("retries") or 0) + 1
        platforms = sorted(failed)
        res = self.publisher.publish_photos(post["run_id"], run.get("topic_id"), post["kind"], frames, caption=caption,
                                            platforms=platforms, retry_tag=f"retry{attempt}-{'-'.join(platforms)}",
                                            **({"first_comment": comment} if comment else {}))
        new_detail = dict(detail, retries=attempt, last_retry={k: res.get(k) for k in ("status", "error", "post_urls")})
        if res.get("status") == "published":
            new_detail["failed"] = {}
            self.db.update_scheduled_post(post["id"], "published", detail=new_detail)
            label = html.escape(self.db.run_label(post["run_id"]))
            return f"🔁 {', '.join(platforms)} retried for {label}: posted."
        self.db.update_scheduled_post(post["id"], post["status"], detail=new_detail)
        return f"🔁 Retry on {', '.join(platforms)} ended with status '{html.escape(str(res.get('status')))}': {html.escape(str(res.get('error') or '')[:200])}"

    def _reel_failed_platforms(self, run_id: str) -> Tuple[List[str], int]:
        """The platforms a Reel did not reach, from its publication receipts (a partial Upload-Post outcome
        names them; a merged result lists missing_platforms), and how many Reel retries were made."""
        platforms: set = set()
        posted: set = set()  # a platform any receipt shows posted is never sent the Reel again
        retries = 0
        for pub in self.db.list_publications(run_id):
            try:
                payload = json.loads(pub.get("response_payload_json") or "{}")
            except ValueError:
                continue
            if payload.get("retry"):
                retries += 1
                if pub.get("status") == "published":
                    platforms -= set(payload.get("platforms") or [])
                continue
            channel = str(pub.get("channel") or "")
            if ":" in channel or channel == "dry-run":  # Stories, carousel, captions: not the Reel's own receipts
                continue
            if pub.get("status") in ("unknown", "failed"):
                failed = set((payload.get("failed") or {}).keys())
                if not failed and not payload.get("post_urls") and pub.get("status") == "failed":
                    # Failed outright: no platform took it, so every platform it was sent to is missing (the retry
                    # said "Every platform has this Reel; nothing to retry": production, 2026-10-26)
                    sent = (payload.get("envelope") or {}).get("platforms") or payload.get("platforms")
                    failed = set(sent or (["youtube"] if pub.get("channel") == "youtube-shorts" else ["facebook", "instagram"]))
                platforms |= failed
            platforms |= set(payload.get("missing_platforms") or [])
            for p in (payload.get("post_urls") or {}):
                posted.add(p)
        return sorted(platforms - posted), retries

    @staticmethod
    def facebook_video_id(db, run_id: str) -> Optional[str]:
        """The posted Facebook Reel's video id, from its link in the publication receipts."""
        for pub in db.list_publications(run_id):
            try:
                url = (json.loads(pub.get("response_payload_json") or "{}").get("post_urls") or {}).get("facebook") or ""
            except ValueError:
                continue
            m = re.search(r"/(?:reel|videos)/(\d+)", str(url))
            if m and pub.get("status") == "published":
                return m.group(1)
        return None

    def add_facebook_captions(self, run_id: str) -> str:
        """Uploads the run's caption file (its SRT, the same words as the burned-in captions) to its posted
        Facebook Reel as closed captions (operator, 2026-10-25: try Meta's caption endpoint on a posted Reel)."""
        from src.publisher import FacebookPageReelsPublisher, NotConfigured, PublishFailed
        run = self.db.get_run(run_id) or {}
        video_id = self.facebook_video_id(self.db, run_id)
        if not video_id:
            return "💬 No posted Facebook Reel is recorded for that run."
        master = Path(run.get("master_video_path") or "")
        srt = master.with_suffix(".srt")
        if not srt.is_file():
            return f"💬 The run's caption file ({html.escape(srt.name)}) is no longer kept."
        try:
            res = FacebookPageReelsPublisher().upload_captions(video_id, srt)
        except NotConfigured:
            return "💬 Facebook captions need META_PAGE_ID and a Page access token in production's .env."
        except PublishFailed as e:
            self.db.record_publication(run["topic_id"], run_id, "facebook:captions", "failed", {"error": str(e)[:500]})
            return f"💬 Facebook did not take the captions: {html.escape(graph_reason(e))}"
        self.db.record_publication(run["topic_id"], run_id, "facebook:captions", "published",
                                   {k: v for k, v in res.items() if k != "result"})
        return (f"💬 Captions added to the Facebook Reel ({'replaced' if res.get('replaced') else 'new'}, "
                f"{res['locale']}). Check them with the CC button on the Reel.")

    def _facebook_finishing(self, run_id: str) -> None:
        """The chosen cover and the closed captions on a Reel just posted to Facebook, automatically (operator,
        2026-10-27: the cover button worked; make it automatic). Best-effort: a refusal is alerted once and the
        episode keeps its buttons; it never touches the post itself. Skipped where either is already done."""
        if not self.facebook_video_id(self.db, run_id):
            return
        from src.publisher import FacebookPageReelsPublisher
        if not FacebookPageReelsPublisher().configured():
            return  # no Page token: the episode's buttons say what to add
        done = {p.get("channel") for p in self.db.list_publications(run_id) if p.get("status") == "published"}
        problems = []
        for channel, action, ok in (("facebook:cover", self.set_facebook_cover, "🖼 Cover set"),
                                    ("facebook:captions", self.add_facebook_captions, "💬 Captions added")):
            if channel in done:
                continue
            try:
                reply = action(run_id)
            except Exception as e:  # never costs the Reel anything
                reply = f"{channel}: {type(e).__name__}: {str(e)[:200]}"
            if not reply.startswith(ok):
                problems.append(reply)
        if problems:
            # What did not happen and what to do, not Graph's whole reply with its URL (operator, 2026-10-27)
            _notice(self, f"⚠️ <b>Facebook finishing</b> for {html.escape(self.db.run_label(run_id))}\n\n"
                        "The Reel posted fine; this extra step on Facebook did not:\n" + "\n".join(problems)
                        + "\n\nTry it again from the episode: 🗓 Posting, its number, then the button.", section="info", run=run_id)

    def set_facebook_cover(self, run_id: str) -> str:
        """Sets the run's chosen cover (thumbnail_cover.jpg, 9:16 as the Reel) on its posted Facebook Reel."""
        from src.publisher import FacebookPageReelsPublisher, NotConfigured, PublishFailed
        run = self.db.get_run(run_id) or {}
        video_id = self.facebook_video_id(self.db, run_id)
        if not video_id:
            return "🖼 No posted Facebook Reel is recorded for that run."
        cover = Path(run.get("thumbnail_cover_path") or Path(run.get("master_video_path") or "").parent / "thumbnail_cover.jpg")
        if not cover.is_file():
            return f"🖼 The run's cover ({html.escape(cover.name)}) is no longer kept."
        try:
            res = FacebookPageReelsPublisher().set_cover(video_id, cover)
        except NotConfigured:
            return ("🖼 The Facebook cover needs our own Page access token (META_PAGE_ACCESS_TOKEN) in production's "
                    ".env; the Page id comes from UPLOAD_POST_FACEBOOK_PAGE_ID unless META_PAGE_ID is set.")
        except PublishFailed as e:
            self.db.record_publication(run["topic_id"], run_id, "facebook:cover", "failed", {"error": str(e)[:500]})
            return f"🖼 Facebook did not take the cover: {html.escape(graph_reason(e))}"
        self.db.record_publication(run["topic_id"], run_id, "facebook:cover", "published",
                                   {k: v for k, v in res.items() if k != "result"})
        return "🖼 Cover set on the Facebook Reel. It can take a few minutes to show; check the Reel on the Page."

    @staticmethod
    def youtube_short_id(db, run_id: str) -> Optional[str]:
        """The video id of the run's posted YouTube Short, from its recorded link."""
        for pub in db.list_publications(run_id):
            if pub.get("channel") != "youtube-shorts" or pub.get("status") != "published":
                continue
            try:
                url = (json.loads(pub.get("response_payload_json") or "{}").get("post_urls") or {}).get("youtube") or ""
            except ValueError:
                continue
            found = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", url) or re.search(r"shorts/([A-Za-z0-9_-]{6,})", url)
            if found:
                return found[1]
        return None

    def set_youtube_thumbnail(self, run_id: str) -> str:
        """Sets the run's chosen cover as its posted Short's thumbnail again (after the channel is verified: the
        first try after posting is refused until then, operator, 2026-10-27)."""
        from src.publisher import UploadPostPublisher
        run = self.db.get_run(run_id) or {}
        video_id = self.youtube_short_id(self.db, run_id)
        if not video_id:
            return "▶️ No posted YouTube Short is recorded for that run."
        cover = Path(run.get("thumbnail_cover_path") or Path(run.get("master_video_path") or "").parent / "thumbnail_cover.jpg")
        if not cover.is_file():
            return f"▶️ The run's cover ({html.escape(cover.name)}) is no longer kept."
        res = UploadPostPublisher().set_youtube_thumbnail(video_id, cover)
        self.db.record_publication(run["topic_id"], run_id, "youtube:thumbnail", "published" if res.get("set") else "failed",
                                   {k: v for k, v in res.items() if k != "thumbnails"})
        if res.get("set"):
            return ("▶️ Standard thumbnail set on the YouTube Short (search and the watch page). The Shorts tab shows a "
                    "frame of the video instead; that one is changed only in the YouTube app: the Short, ✏️ Edit, Thumbnail.")
        error = str(res.get("error") or "")
        hint = (" The channel must be verified for custom thumbnails (youtube.com/verify)." if "403" in error else "")
        return f"▶️ YouTube did not take the thumbnail: {html.escape(error[:300])}.{hint}"

    def rebook_followers(self, run_id: str) -> str:
        """Books again the Stories and carousel of a Reel that posted after all (they were cancelled when its slot
        failed: operator, 2026-10-26, the Stories never went to Facebook or Instagram). The Stories go 45 minutes
        from now, the carousel in its next slot; never while either is still booked or already posted."""
        run = self.db.get_run(run_id) or {}
        if run.get("status") != "published" or not run.get("master_video_path"):
            return "📱 The Reel must be posted first; its Stories and carousel follow it."
        live = [p for p in self.db.list_scheduled_posts(("pending", "waiting", "published", "unknown"), run_id=run_id)
                if p["kind"] in ("story", "carousel")]
        if live:
            return "📱 Its Stories and carousel are already booked or posted: see Posting."
        lines = self._book_followers(run_id, Path(run["master_video_path"]).parent, datetime.now(timezone.utc))
        if not lines:
            return "📱 This episode has no Stories frames or carousel slides to post."
        return "📱 Booked again:\n" + "\n".join(html.escape(x) for x in lines)

    def post_youtube_short(self, run_id: str) -> str:
        """The YouTube Short of a Reel that posted without one (a Reel posted by Retry, or a Short that failed):
        never when a Short is posted or its outcome is unknown, so nothing goes up twice."""
        run = self.db.get_run(run_id) or {}
        if not run.get("master_video_path"):
            return "▶️ That run has no rendered Reel."
        shorts = [p for p in self.db.list_publications(run_id) if p.get("channel") == "youtube-shorts"]
        if any(p.get("status") == "published" for p in shorts):
            return "▶️ The YouTube Short is already posted."
        if any(p.get("status") == "unknown" for p in shorts):
            return "▶️ The last YouTube Short's outcome is unknown: check YouTube Studio first, so it never goes up twice."
        if not config.YOUTUBE_SHORTS:
            return "▶️ YouTube Shorts are turned off (YOUTUBE_SHORTS)."
        envelope = self.publisher.build_envelope(run_id, Path(run["master_video_path"]), Path(run["thumbnail_cover_path"]),
                                                 Path(run["thumbnail_grid_path"]), run["script_payload"])
        short = self.publisher.publish_short(envelope, dry_run=False)
        if short.get("status") == "published":
            links = " ".join(html.escape(str(v)) for v in (short.get("post_urls") or {}).values())
            return f"▶️ YouTube Short posted ({html.escape(config.YOUTUBE_SHORTS_PRIVACY)}). {links}"
        return f"▶️ YouTube Short not posted ({html.escape(str(short.get('status')))}): {html.escape(str(short.get('error') or '')[:300])}"

    def retry_reel(self, run_id: str) -> str:
        """Re-sends a Reel only to the platform(s) it did not reach, under its own request key (operator,
        2026-10-24: the Reel path had no targeted re-send; a partial ended as "unknown" with a question)."""
        run = self.db.get_run(run_id)
        if not run or not run.get("master_video_path"):
            return "🔁 That run has no rendered Reel."
        platforms, retries = self._reel_failed_platforms(run_id)
        if not platforms:
            return "🔁 Every platform has this Reel; nothing to retry."
        if retries >= 2:
            return "🔁 This Reel has been retried twice; check the platforms by hand."
        last = next((p for p in reversed(self.db.list_publications(run_id)) if "retry" in (p.get("response_payload_json") or "")), None)
        if last and last.get("status") == "unknown":
            return "🔁 The last Reel retry's outcome is unknown: check the platform before another, so nothing posts twice."
        try:   # a tap tries the fact re-check once more even after its automatic tries ran out
            self._require_release(run, dry_run=False, retry_now=True)
        except ValueError as e:
            return self._blocked_reply("🔁", run_id, e, verb="retried")
        except Exception as e:  # noqa: BLE001 - the checker or network: nothing was sent
            return (f"🔁 Not retried: it could not be checked just now ({html.escape(_error_words(e))}). Nothing was "
                    "sent; tap 🔁 Retry Reel again later.")
        payload = run["script_payload"]
        envelope = self.publisher.build_envelope(run_id, Path(run["master_video_path"]), Path(run["thumbnail_cover_path"]),
                                                 Path(run["thumbnail_grid_path"]), payload, platforms=platforms)
        tag = f"retry{retries + 1}-{'-'.join(platforms)}"
        envelope["request_id"] = f"{run_id}-{tag}"
        res = self.publisher.publish_reel(envelope, dry_run=False)
        self.db.record_publication(run["topic_id"], run_id, "reel-retry", res.get("status", "unknown"),
                                   {"retry": tag, "platforms": platforms, **{k: res.get(k) for k in ("post_urls", "error", "failed", "ok")}})
        label = html.escape(self.db.run_label(run_id))
        if res.get("status") == "published":
            if run.get("status") in ("publish_unknown", "failed", "scheduled", "approved"):
                self.db.update_run(run_id, "published")
                self.topic_engine.record_status(run["topic_id"], "published", run_id=run_id)
            now = datetime.now(timezone.utc)
            # Its booking says it posted, and the Stories and carousel cancelled when the Reel failed are booked
            # again after it (production, 2026-10-26: a failed slot, then a retry, left them cancelled for good)
            for booking in self.db.list_scheduled_posts(("failed", "unknown"), run_id=run_id):
                if booking["kind"] == "reel":
                    self.db.update_scheduled_post(booking["id"], "published", detail={"posted_at": now.isoformat(), "retry": tag})
            followers = [p for p in self.db.list_scheduled_posts(("pending", "waiting", "published"), run_id=run_id)
                         if p["kind"] in ("story", "carousel")]
            booked = []
            if not followers and POST_SCHEDULING:
                booked = self._book_followers(run_id, Path(run["master_video_path"]).parent, now)
            links = ", ".join(f"{html.escape(k)}: {html.escape(str(v))}" for k, v in (res.get("post_urls") or {}).items())
            then = ("\n" + "\n".join(html.escape(x) for x in booked)) if booked else ""
            self._facebook_finishing(run_id)  # a Reel posted by Retry gets its cover and captions too
            short = ""
            if config.YOUTUBE_SHORTS and (payload.get("delivery_profile") or "reel_9x16") == "reel_9x16":
                short = "\n" + self.post_youtube_short(run_id)   # it went only with a publish that succeeded first time
            return f"🔁 Reel re-sent to {', '.join(platforms)} for {label}: posted. {links}{then}{short}"
        return f"🔁 Reel retry on {', '.join(platforms)} ended with status '{html.escape(str(res.get('status')))}': {html.escape(str(res.get('error') or '')[:200])}"

    def promote_run(self, run_id: str, now: bool = False) -> str:
        """Your per-video go-ahead: a finished dry run (simulated) is posted for real while the daemon
        stays in dry-run mode. The live release gate decides first. With posting slots on, the Reel is
        booked and this daemon posts it when due (from a dry-run daemon only promoted runs post);
        with `now`, or without slots, it posts at once and its Stories and carousel are booked after it.
        Returns what happened, in words (never raises for the operator)."""
        run = self.db.get_run(run_id)
        if not run:
            return "🚀 That run is no longer recorded."
        if run["status"] not in PROMOTABLE_STATUSES:
            return f"🚀 That run is {run['status'].replace('_', ' ')}; only a finished dry run (simulated) can be posted."
        from src.release import require_release
        try:
            require_release(run, dry_run=False)
        except ValueError as e:
            return f"🚀 Not posted: {html.escape(str(e))}"
        self.db.set_setting(PROMOTED_PREFIX + run_id, datetime.now(timezone.utc).isoformat())
        label = html.escape(self.db.run_label(run_id))
        try:
            if self.schedule_posts and POST_SCHEDULING and run.get("content_format") != "deep_dive" and not now:
                res = self.schedule_run(run_id)
                when = post_schedule.fmt(datetime.fromisoformat(res["scheduled_for"]))
                return (f"🚀 <b>{label}</b> is booked for real posting: Reel at {html.escape(when)}, Stories after it, "
                        "the carousel in its next slot. This daemon posts it when due; you get the receipt here.")
            res = self.publish_run(run_id, dry_run=False, previous_status="published" if run.get("times_published") else "queued")
        except ValueError as e:
            self.db.set_setting(PROMOTED_PREFIX + run_id, "")
            return f"🚀 Not posted: {html.escape(str(e))}"
        if res.get("status") == "published":
            followers = []
            if POST_SCHEDULING and run.get("content_format") != "deep_dive" and run.get("master_video_path"):
                try:  # the Reel is out; its Stories and carousel follow in their slots, posted by the daemon
                    followers = self._book_followers(run_id, Path(run["master_video_path"]).parent, datetime.now(timezone.utc))
                except Exception as e:
                    log.warning("Could not book the Stories and carousel after posting %s: %s", run_id, e)
            return f"🚀 <b>{label}</b> is posted." + ("\n" + "\n".join(html.escape(x) for x in followers) if followers else "")
        return f"🚀 <b>{label}</b>: publishing ended with status '{html.escape(str(res.get('status')))}'; see the receipt above."

    def _may_post(self, run_id: str) -> bool:
        """From a dry-run daemon only a run you promoted posts; a live daemon posts every booked run."""
        return (not self.only_promoted) or bool(self.db.get_setting(PROMOTED_PREFIX + run_id))

    def voice_inputs(self, script: str, dry_run: bool) -> Dict[str, Any]:
        """What a voiceover depends on (its checkpoint inputs): a change to any of these re-voices.
        Real runs need a licensed voice; dry runs don't spend ElevenLabs credits."""
        return {"script": script, "voice": self.audio_engine.voice, "rate": self.audio_engine.rate,
                "pitch": self.audio_engine.pitch, "eleven_voice": self.audio_engine.eleven_voice,
                "eleven_model": config.ELEVENLABS_MODEL,
                # Takes made before 2026-10-25 may have been time-stretched (heard as awkward): never reused.
                "take": "natural",
                "licensed_only": (not dry_run) and config.VOICE_REQUIRE_LICENSED,
                "allow_paid": (not dry_run) or config.ELEVENLABS_IN_DRY_RUNS or self.paid_voice_in_dry_run}

    @staticmethod
    def voice_reusable(checkpoints, voice_inputs: Dict[str, Any], saved_voice: Dict[str, Any], dry_run: bool) -> bool:
        """Whether the run's voiceover is kept. A real run never reuses an unlicensed (test) voiceover; a
        licensed one already paid for this exact script satisfies any run (a resume with paid voice off
        re-voiced it with the free voice, and a live run would have paid for it again)."""
        if checkpoints.reusable("voice", voice_inputs):
            return dry_run or not config.VOICE_REQUIRE_LICENSED or bool(saved_voice.get("licensed"))
        if saved_voice.get("licensed") and any(
                checkpoints.reusable("voice", {**voice_inputs, "allow_paid": True, "licensed_only": lic}) for lic in (True, False)):
            log.info("Keeping the licensed voiceover already made for this script.")
            return True
        return False

    def _produce_and_publish(self, topic, run_id, run_dir, auto_approve, dry_run, previous_status) -> Dict[str, Any]:
        self._check_cancel(run_id)
        self.progress_msg_id = None
        feedback = self._feedback()
        if feedback.get("reason"):  # the progress card says this is a regeneration, and why
            from src.improve import REASONS
            self.db.set_setting("regenerating:" + run_id,
                                REASONS.get(feedback["reason"], feedback["reason"])
                                + (f": {clip_words(feedback['text'], 160)}" if feedback.get("text") else ""))  # at a word, with "…"
        try:
            self.progress_msg_id = self.telegram_bot.send_progress_card(run_id, topic["title"])
        except Exception:
            pass

        self._check_cancel(run_id)
        # 1. Visual assets first: fail before spending on LLM/TTS calls. Any photo never looked at (added after
        # an approval) is screened first, so the video uses only photos with a verdict (2026-10-25).
        still_turned = self._upright_photos(topic)  # before screening: a photo fetched again is screened as it is now
        self._screen_photos(topic)
        scene_media = self.visual_engine.get_scene_sequence(topic)
        if still_turned:  # never used turned over
            scene_media = [m for m in scene_media if Path(str(m)).name not in still_turned]
        self._still_turned = set(still_turned or ())  # the reserve photos (_reserve_stills) skip these too
        # Photos with text printed into them (a placard, a caption strip) are set aside, looked at once each.
        scene_media = beat_images.without_burned_in_text(scene_media, topic.get("title", ""), config.MIN_SCENE_IMAGES)
        from src.image_review import strong_stems
        self._strong_stems = strong_stems(self.visual_engine.assets_dir / topic["id"])  # Stories, carousel: no context
        hero_img = self.visual_engine.hero_image(scene_media)
        log.info("Scene media: %d items.", len(scene_media))

        from src.checkpoints import Checkpoints, file_inputs, topic_inputs
        from src import config as pipeline_config
        checkpoints = Checkpoints(run_dir)
        timer, t0 = StageTimer(run_dir), time.perf_counter()
        script_inputs = {"topic": topic_inputs(topic), "engine": pipeline_config.SCRIPT_ENGINE,
                         "fact_sheet": file_inputs([pipeline_config.DATA_DIR / "fact_sheets" / f"{topic['id']}.json"])}
        saved_script = run_dir / "verified_script.json"
        saved = json.loads(saved_script.read_text(encoding="utf-8")) if saved_script.exists() else {}
        too_long_now = (saved.get("guardrail_passed") and checkpoints.reusable("script", script_inputs)
                        and self._spoken_length_now(saved))
        kept_words = False  # a script carried over word for word: its voiceover carries over too
        if too_long_now:
            # A re-render of a run whose take ran over: the saved script is shortened for the voice's measured
            # pace (ElevenLabs is never stretched), then fact-checked again as any revision is.
            log.info("The saved script runs long for this voice; revising it: %s", too_long_now[:160])
            payload = self._write_and_check_script(topic, run_id, run_dir, dry_run,
                                                   initial_payload=saved, initial_notes=too_long_now)
            saved_script.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            checkpoints.complete("script", script_inputs, [saved_script])
        elif saved.get("guardrail_passed") and checkpoints.reusable("script", script_inputs):
            payload = saved
            log.info("Resuming with verified script inputs and intact metadata.")
        else:
            fb = self._feedback()
            if (fb.get("reason") in SCRIPT_REASONS or revises_script(fb)) and fb.get("previous"):
                # Regenerate after your review: the previous script is revised for what you said,
                # instead of being rewritten blind.
                note = (f"The human reviewer sent the previous version back. Reason: {REASONS.get(fb['reason'], fb['reason'])}."
                        + (f" Their words: {fb['text']}" if fb.get("text") else "")
                        + " Revise the script to fix this, using only the verified facts.")
                payload = self._write_and_check_script(topic, run_id, run_dir, dry_run,
                                                       initial_payload=fb["previous"], initial_notes=note)
            else:
                kept = self._script_from_previous(fb)
                library = None if kept is not None or fb.get("from_run") else self._script_from_library(topic)
                if kept is not None:
                    # The review was about pictures, sound or the cover: the script it passed stays word for
                    # word, so its voiceover carries over too (a regeneration rewrote it, 2026-10-24).
                    payload, kept_words = kept, True
                    log.info("Script kept from %s: %s.", fb.get("from_run") or self.reuse_from,
                             f"the review was about {fb.get('reason')}, not the words" if fb.get("from_run") else "reused as asked")
                elif library is not None:
                    payload = self._library_payload(topic, run_id, run_dir, dry_run, library)
                else:
                    payload = self._write_and_check_script(topic, run_id, run_dir, dry_run)
            saved_script.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            checkpoints.complete("script", script_inputs, [saved_script])

        from src.fact_evidence import current_fact_review
        if not current_fact_review(payload):
            try:
                self._verify_final_copy(payload, topic)
            except ValueError as exc:
                payload = self._write_and_check_script(topic, run_id, run_dir, dry_run,
                                                       initial_payload=payload, initial_notes=str(exc))
            saved_script.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            checkpoints.complete("script", script_inputs, [saved_script])

        # Before the paid voice, each sentence against the photos the video can show (audit 2026-10-28): a
        # concrete line no photo shows goes back to the writer once, while it is still only words.
        checked = self._lines_against_pictures(topic, run_id, run_dir, dry_run, payload, scene_media,
                                               checkpoints, kept_words)
        # QF-14 (operator ruling 2026-11-10): an opening that ships without an early signal is flagged on the review
        # card for the operator to judge; the flag follows the words the run will voice.
        # QF-15 (operator ruling 2026-11-10): likewise a payoff that names none of the opening's nouns.
        # QF-16 (ruling 2026-11-11): likewise an opening, payoff or loop that keeps a qualifier.
        from src import fact_sheet
        from src.script_engine import hedge_note, hook_signal_note, payoff_note
        hedge_sheet = (fact_sheet.load(checked.get("topic_id") or topic.get("id") or "", max_age_days=3650)
                       if checked.get("fact_sheet") else None)
        for key, flag in (("hook_signal_note", hook_signal_note(checked.get("narration_script") or "")),
                          ("payoff_note", payoff_note(checked.get("narration_script") or "",
                                                      checked.get("payoff_sentence") or "")),
                          ("hedge_note", hedge_note(checked.get("narration_script") or "", hedge_sheet))):
            if flag != (checked.get(key) or ""):
                checked = {k: v for k, v in checked.items() if k != key} | ({key: flag} if flag else {})
        if checked is not payload:
            payload = checked
            saved_script.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            checkpoints.complete("script", script_inputs, [saved_script])

        timer.record("script_and_fact_check", time.perf_counter() - t0)
        if getattr(self, "progress_msg_id", None):
            try:
                self.telegram_bot.update_progress_card(self.progress_msg_id, 1, 5, topic["title"], run_id, "Fact check & script verified")
            except Exception:
                pass

        # Before the paid voice (decisions/QF-58-gate.md, Option A): the pool is refilled for the photo beats the
        # checked script will have (scene_planner.estimated_photo_beats; one bounded search, not repeated within
        # the refresh period), and a Reel whose pool is no longer ready at the floor (photos removed or replaced
        # since the run started) stops here, with its checked script saved, before any voice is paid for.
        tid = topic["id"]
        if config.VIDEO_HEIGHT > config.VIDEO_WIDTH and (ASSETS_DIR / tid / "credits.json").is_file():
            from src.asset_pipeline import ensure_pool, not_ready_reason, pool_ready
            from src.scene_planner import (
                estimated_photo_beats,
                script_asks_for_graphics,
            )
            from src.visual_engine import MissingAssetsError
            expected = estimated_photo_beats(payload.get("narration_script") or "", script_asks_for_graphics(payload))
            if not ensure_pool(topic, ASSETS_DIR, beats=[{}] * expected) and not pool_ready(tid, ASSETS_DIR):
                self.db.update_run(run_id, "producing", script_payload=payload)
                raise MissingAssetsError(f"Photo pool for {tid} not ready before the voice: {not_ready_reason(tid, ASSETS_DIR)}")

        # 4-5. Audio (reused when resuming)
        self._check_cancel(run_id)
        from src.audio_engine import VOICEOVER_FILE, voiceover_file
        words_path = run_dir / "words.json"
        voice_path = run_dir / "voice.json"  # which provider made it, and whether it's licensed
        voice_inputs = self.voice_inputs(payload["narration_script"], dry_run)
        # A regeneration keeps its paid voice, unless this run would be voiced by another provider now.
        self._seed_voice_from_previous(run_dir, payload["narration_script"],
                                       wanted=self.audio_engine.preferred_provider(voice_inputs["allow_paid"]))
        vo_path = voiceover_file(run_dir)  # after the carry-over: the WAV, or a take made before QF-08
        saved_voice = json.loads(voice_path.read_text(encoding="utf-8")) if voice_path.exists() else {}
        # A real run never reuses an unlicensed (test) voiceover. Older runs without voice.json
        # predate this record, so they are re-voiced too.
        # A narration a little over the limit is spoken slightly faster (the script stage accepted it so).
        from src.script_engine import fit_speed
        speech_speed = fit_speed(payload["narration_script"]) or 1.0
        if speech_speed != 1.0 and self.audio_engine.preferred_provider(voice_inputs["allow_paid"]) == "elevenlabs":
            # Production's ElevenLabs model ignores its speed setting (a take asked to be 5% faster read 1%
            # slower), so it is never asked (the script stage already plans its narration to fit as written).
            speech_speed = 1.0
        if speech_speed != 1.0:  # a voiceover made at another speed is not reused (normal ones keep their inputs)
            voice_inputs["speed"] = speech_speed
        licensed_req, allow_paid = voice_inputs["licensed_only"], voice_inputs["allow_paid"]
        t0 = time.perf_counter()
        reusable = self.voice_reusable(checkpoints, voice_inputs, saved_voice, dry_run)
        if reusable and vo_path.exists():
            from src.script_engine import duration_problem as _too_long
            if _too_long(self.audio_engine.get_duration(vo_path)):  # a saved take that failed the limit: made again
                log.info("The saved voiceover does not fit the length limit; making it again.")
                reusable = False
        if reusable:
            words = json.loads(words_path.read_text(encoding="utf-8"))
            log.info("Resuming with the existing voiceover.")
        else:
            from src.audio_engine import calibrate_pace
            from src.script_engine import refit_speed
            vo_path = run_dir / VOICEOVER_FILE  # a new take is always the WAV (QF-08)

            def voice_at(speed):
                made = self.audio_engine.generate_narration(payload["narration_script"], vo_path, licensed_only=licensed_req,
                                                            allow_paid=allow_paid, speed=speed)
                calibrate_pace(self.audio_engine.last_provider,
                               self.audio_engine.eleven_voice if self.audio_engine.last_provider == "elevenlabs" else self.audio_engine.voice,
                               payload["narration_script"], self.audio_engine.get_duration(vo_path), speed=speed)
                return made
            words = voice_at(speech_speed)
            # A read that came out over the ceiling (ElevenLabs varies about 5% between takes: production's
            # 2026-10-25 run read 114 words in 53.7 s against 52). A voice that honours its rate setting (Azure,
            # Edge) is made once more at the rate the overrun needs; ElevenLabs once more as it is. A take is
            # never time-stretched (the operator heard a 4% stretch as awkward, 2026-10-25).
            words, speech_speed = self._fit_take(vo_path, words, speech_speed, voice_at, voice_inputs)
            words_path.write_text(json.dumps(words), encoding="utf-8")
            voice_path.write_text(json.dumps({"provider": self.audio_engine.last_provider,
                                              "licensed": self.audio_engine.last_licensed,
                                              **({"first_take": self.last_take_band}
                                                 if getattr(self, "last_take_band", None) else {})}), encoding="utf-8")
            from src.audio_provenance import record_voice
            record_voice(vo_path, self.audio_engine.last_provider, self.audio_engine.last_licensed,
                         payload["narration_script"])
            checkpoints.complete("voice", voice_inputs, [vo_path, words_path, voice_path])
            if self.audio_engine.fallback_reason:
                r_label = html.escape(self.db.run_label(run_id))
                _notice(self, f"🔁 <b>Voice fallback</b>\n\n<b>{r_label}</b>\n\n{html.escape(self.audio_engine.fallback_reason)}\n\nVoiced by: <b>{html.escape(self.audio_engine.last_provider)}</b>.", section="info")
        v_prov = (saved_voice.get("provider") if reusable else self.audio_engine.last_provider) or "unknown"
        v_lic = saved_voice.get("licensed") if reusable else self.audio_engine.last_licensed
        lic_badge = "🟢 Commercially Licensed" if v_lic else "🟡 Unlicensed (Review / Test Only)"
        payload["voice_note"] = (f"🎙️ Voice: {v_prov.title()} ({lic_badge})"
                                 + (f", read {round((speech_speed - 1) * 100)}% faster to fit the time limit"
                                    if speech_speed > 1 else ""))
        vo_dur = self.audio_engine.get_duration(vo_path)
        timer.record("voiceover", time.perf_counter() - t0)
        log.info("Voiceover: %.2fs, %d words timed.", vo_dur, len(words))
        if getattr(self, "progress_msg_id", None):
            try:
                self.telegram_bot.update_progress_card(self.progress_msg_id, 2, 5, topic["title"], run_id,
                                                       f"voiceover {'reused' if reusable else 'made'}, {vo_dur:.1f} s long")
            except Exception:
                pass
        # Checked before any rendering: the master is exactly as long as the voiceover.
        from src.script_engine import duration_problem
        too_long_or_short = duration_problem(vo_dur)
        if too_long_or_short:
            raise RuntimeError(too_long_or_short)
        # Image preparation (upscaling, face privacy) needs neither music nor the bed, so it runs
        # while they are made. It starts only once the voiceover has passed, as before.
        prep_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="image-prep")
        prepared = prep_pool.submit(self._prepare_images, scene_media, hero_img, run_dir)
        prep_pool.shutdown(wait=False)  # the worker finishes its one job; the caller collects it
        try:
            return self._produce_bed_and_render(topic, run_id, run_dir, payload, scene_media, hero_img,
                                                vo_path, words_path, vo_dur, words, checkpoints, file_inputs,
                                                auto_approve, dry_run, previous_status, prepared)
        except BaseException:
            if not prepared.done():
                log.info("Run stopped; waiting for image preparation to finish before cleaning up.")
            concurrent.futures.wait([prepared])
            raise

    def _fit_take(self, vo_path, words, speech_speed: float, voice_at, voice_inputs) -> Tuple[List[Dict[str, Any]], float]:
        """One more take when the first ran over the ceiling, never a time-stretch (the operator heard a 4%
        stretch as awkward, 2026-10-25). A voice that honours its rate (Azure, Edge) is read at the rate the
        overrun needs, within SPEECH_SPEEDUP_MAX; ElevenLabs, which ignores its speed setting, is simply read
        again (its takes vary by about 5%). A take still over is left for the length check, which stops the
        run; its re-render then shortens the script for the pace just measured. Returns (words, speed)."""
        from src.audio_engine import honours_speed
        from src.script_engine import TARGET_DURATION_MIN, refit_speed, take_band
        seconds = self.audio_engine.get_duration(vo_path)
        # The take against the band its script was planned for (rc72: planned on the middle half of the takes, so
        # how often a take falls outside it is watched): inside, short (read again), long but kept, or over.
        band = take_band(seconds)
        self.last_take_band = {"seconds": round(seconds, 2), "band": band}
        log.info("Voice take: %.1fs, %s (planned %g-%g s; kept up to %g s).", seconds, band, TARGET_DURATION_MIN,
                 config.HARD_DURATION_CEILING, config.VOICE_DURATION_CEILING)
        if band != "inside":
            from src import improve
            improve.note("voice.take_band", f"first take {seconds:.1f}s: {band}", source="voice")
        if seconds < TARGET_DURATION_MIN:
            # A take a little under the minimum: ElevenLabs' takes vary (production 2026-10-25: 93 words read
            # 41.1 s), so it is read once more. A voice that reads at its set rate would only repeat itself.
            if not honours_speed(self.audio_engine.last_provider) and seconds * TAKE_SPREAD >= TARGET_DURATION_MIN:
                log.info("Voiceover ran %.1fs, under the minimum; reading it once more (takes vary).", seconds)
                return voice_at(speech_speed), speech_speed
            return words, speech_speed
        if seconds <= config.VOICE_DURATION_CEILING:  # a take past the planning ceiling is kept up to this
            return words, speech_speed
        if honours_speed(self.audio_engine.last_provider):
            faster = refit_speed(seconds, speech_speed)
            if not faster:
                log.warning("Voiceover ran %.1fs; fitting it would need more than %.0f%% faster, so it is not "
                            "made again.", seconds, (config.SPEECH_SPEEDUP_MAX - 1) * 100)
                return words, speech_speed
            log.info("Voiceover ran %.1fs; making it once more %.0f%% faster to fit.", seconds, (faster - 1) * 100)
            voice_inputs["speed"] = faster
            return voice_at(faster), faster
        if seconds > config.VOICE_DURATION_CEILING * config.SPEECH_SPEEDUP_MAX:
            log.warning("Voiceover ran %.1fs; another take would not fit, so it is not made.", seconds)
            return words, speech_speed
        log.info("Voiceover ran %.1fs; reading it once more (takes vary), at its natural speed.", seconds)
        return voice_at(speech_speed), speech_speed

    def _spoken_length_now(self, saved: Dict[str, Any]) -> str:
        """Why a saved script would run too long or short when spoken at the voice's current measured pace,
        or "" (also when it can't be measured)."""
        from src.models import ScriptPackage
        from src.script_engine import spoken_length_problems
        try:
            return " ".join(spoken_length_problems(ScriptPackage.model_validate(saved)))
        except Exception as e:  # a resume must not fail on the measurement
            log.debug("Spoken length of the saved script not measured: %s", e)
            return ""

    @staticmethod
    def _prepare_images(scene_media, hero_img, run_dir=None):
        """Upscales low-resolution stills and de-identifies living faces (hero is one of the scenes)."""
        t0 = time.perf_counter()
        scene_media, low_res = ensure_all(scene_media)
        scene_media = colour_balance.balance_all(scene_media)   # a gentle cast and exposure balance (2026-10-29)
        hero_img = prepared_still(hero_img)
        *scene_media, hero_img = privacy.anonymize_all([*scene_media, hero_img])
        if run_dir is not None:
            StageTimer(run_dir).record("image_prep", time.perf_counter() - t0)
        return scene_media, hero_img, low_res

    def _produce_bed_and_render(self, topic, run_id, run_dir, payload, scene_media, hero_img, vo_path, words_path,
                                vo_dur, words, checkpoints, file_inputs, auto_approve, dry_run, previous_status, prepared):
        self._check_cancel(run_id)
        bed_path = run_dir / "ambient_bed.wav"
        t0 = time.perf_counter()
        from src.delivery_profiles import get_delivery_profile
        from src.audio_provenance import record_bed, audio_source_inputs
        from src.music import load_library, MUSIC_DIR
        profile = get_delivery_profile(payload.setdefault("delivery_profile", "reel_9x16"))
        library_tracks = load_library()
        music_path = run_dir / "music_selection.json"
        self._seed_bed_from_previous(run_dir, vo_path)  # a regeneration keeps the bed made for its voiceover
        from src.checkpoints import portable_inputs
        from src import end_card
        # A Reel ends on the loop bridge (end_card.LOOP_SECONDS; the 2.9 s end card until 2026-11-03), whose sound
        # the bed carries: the bed is sized to the bridge.
        card = profile.width < profile.height
        bed_seconds = vo_dur + end_card.bed_tail(card)
        # The picture's cuts: the narration's beat boundaries (segment_beats, the grid the scene plan cuts on), for the
        # transition whooshes (QF-05: only on a cut, at most two).
        scene_cuts = caption_cut_times([b["end"] - b["start"] for b in segment_beats(words, vo_dur)])
        bed_inputs = {"voice_and_rules": portable_inputs(run_dir, [vo_path, words_path, Path("src/audio_engine.py"),
                                                                    Path("src/end_card.py"), Path("src/sound_plan.py"),
                                                                    Path("src/music.py")]),
                      "end_card": end_card.LOOP_VARIANT if card else None,
                      "profile": profile.model_dump(),
                      "library": [{k: v for k, v in t.items() if k != "uses"} for t in library_tracks],
                      "library_files": file_inputs([MUSIC_DIR / t["file"] for t in library_tracks]),
                      "selected_sources": audio_source_inputs(bed_path), "scene_cuts": scene_cuts}
        if checkpoints.reusable("bed", bed_inputs):
            music = json.loads(music_path.read_text(encoding="utf-8"))
        else:
            if getattr(self, "progress_msg_id", None):
                try:
                    self.telegram_bot.update_progress_card(
                        self.progress_msg_id, 2, 5, topic["title"], run_id,
                        "Scoring music & sound bed; photos being upscaled and privacy-checked")
                except Exception:
                    pass
            # The sound plan first: its dominant mood chooses the music (QF-84; never a default mood).
            from src import sound_plan as sound_plan_mod
            plan, mood_record = sound_plan_mod.plan_and_mood(words, topic.get("title", ""))  # plan None: word cues
            plan_path = run_dir / "sound_plan.json"
            plan_path.write_text(json.dumps(plan, indent=1), encoding="utf-8")
            music = music_for(dict(payload, topic_id=topic.get("id")), bed_seconds, run_dir,
                              self.audio_engine.voice_quota(),
                              exclude=self._previous_tracks(self._feedback()),  # "Music / sound": not the same track
                              mood_record=mood_record, published=self.db.music_published_uses())
            self.audio_engine.generate_ambient_bed(bed_seconds, bed_path, words=words,
                                                  music_path=music["path"], sr=profile.audio_sample_rate,
                                                  loop_seamless=profile.loop_seamless, sound_plan=plan,
                                                  end_card_at=vo_dur if card else None, scene_cuts=scene_cuts)
            if bed_path.is_file():
                record_bed(bed_path, music, sfx=self.audio_engine.last_sfx_sources)
                music_path.write_text(json.dumps(music, default=str, indent=2), encoding="utf-8")
                bed_inputs["selected_sources"] = audio_source_inputs(bed_path)
                checkpoints.complete("bed", bed_inputs, [bed_path, music_path, plan_path,
                                                         bed_path.with_suffix(".wav.rights.json")])
        payload["music_note"] = music_note(music)
        if music["credit"] and music["credit"] not in payload["caption"]:  # CC BY needs attribution
            payload["caption"] = f"{payload['caption']}\n{music['credit']}"
        # The tracks this bed was made from (a carried-over bed too); a use counts only once the Reel is published.
        self.db.record_music_selection(run_id, music_selection_rows(music))
        StageTimer(run_dir).record("music_and_bed", time.perf_counter() - t0)

        def bed_for_cuts(durations):
            # Picture holds/repairs can remove planned cuts after the first bed was made.
            # Re-mix locally from the selected music and saved plan, without selecting
            # or generating another score, before the master hashes its audio input.
            nonlocal scene_cuts
            actual_cuts = caption_cut_times(durations)
            if actual_cuts == scene_cuts:
                return
            saved_plan = json.loads((run_dir / "sound_plan.json").read_text(encoding="utf-8"))
            self.audio_engine.generate_ambient_bed(
                bed_seconds, bed_path, words=words, music_path=music["path"],
                sr=profile.audio_sample_rate, loop_seamless=profile.loop_seamless,
                sound_plan=saved_plan, end_card_at=vo_dur if card else None,
                scene_cuts=actual_cuts)
            record_bed(bed_path, music, sfx=self.audio_engine.last_sfx_sources)
            scene_cuts = actual_cuts
            bed_inputs["scene_cuts"] = actual_cuts
            bed_inputs["selected_sources"] = audio_source_inputs(bed_path)
            checkpoints.complete("bed", bed_inputs, [bed_path, music_path, run_dir / "sound_plan.json",
                                                     bed_path.with_suffix(".wav.rights.json")])

        return self._render_approve_publish(topic, run_id, run_dir, payload, scene_media, hero_img,
                                            vo_path, bed_path, words, vo_dur, auto_approve, dry_run, previous_status,
                                            prepared, bed_for_cuts)

    def _write_and_check_script(self, topic, run_id, run_dir, dry_run,
                                initial_payload=None, initial_notes="") -> Dict[str, Any]:
        tid = topic["id"]
        revision_notes, payload = initial_notes, initial_payload
        # The hook's form, learned from how earlier Reels' first three seconds did (src/hook_learning.py). The
        # writer is steered; the form it actually wrote is what is measured.
        from src import hook_learning
        hook_form, hook_why = hook_learning.for_run(self.db, run_id, topic)
        from src import engagement_learning
        asks, asks_why = engagement_learning.for_run(self.db, run_id)   # share line and caption question forms
        topic = {**topic, "hook_form": hook_form, "engagement_forms": asks}

        def show(text: str) -> None:  # step 1 is long; the card says which draft it is on
            if getattr(self, "progress_msg_id", None):
                self.telegram_bot.update_progress_card(self.progress_msg_id, 1, 5, topic["title"], run_id, text)
        self.script_engine.progress = show
        self.script_engine.length_run = run_id  # its drafts' length window is this run's (script_engine)
        earlier: List[str] = []  # every draft's rejection, so a later draft can't restate one in new words
        drafts, structural = 0, []  # every draft written, the engine's own structural rejections included
        for revision in range(MAX_FACT_REVISIONS + 1):
            self._check_cancel(run_id)
            payload = self.script_engine.generate_script(topic, revision_notes=revision_notes, previous=payload)
            drafts += getattr(self.script_engine, "last_drafts", 0) or 1
            structural += list(getattr(self.script_engine, "last_draft_problems", None) or [])
            passed, payload, notes = self.script_engine.verify_claims(payload, topic=topic, allow_unreviewed_curated=dry_run)
            log.info("Guardrail (draft %d): %s", revision + 1, notes)
            if passed or payload.get("origin") not in ("claude", "gemini-free"):
                break
            revision_notes = with_earlier_rejections(notes, earlier)
            earlier.append(notes)
        if not passed and payload.get("origin") in ("claude", "gemini-free"):
            # The last draft failed on a sentence or two the checker quoted: those are taken out (never
            # rewritten) and the draft is checked again in full, before the run fails closed.
            from src.script_engine import without_blocked_sentences
            trimmed = without_blocked_sentences(payload, notes)
            if trimmed is not None:
                log.info("Last draft blocked on %d sentence(s); checking it without them: %s",
                         len(trimmed["trimmed_sentences"]), " | ".join(trimmed["trimmed_sentences"])[:300])
                self._check_cancel(run_id)
                t_passed, t_payload, t_notes = self.script_engine.verify_claims(
                    trimmed, topic=topic, allow_unreviewed_curated=dry_run)
                log.info("Guardrail (draft %d without the blocked sentences): %s", revision + 1, t_notes)
                if t_passed:
                    passed, payload, notes = t_passed, t_payload, t_notes
                else:
                    earlier.append(notes)
                    notes = t_notes
        if not passed:
            curated = self.script_engine._load_curated(tid)
            if curated:
                pkg, reviewed = curated
                if reviewed or dry_run:
                    log.info("Claude script revisions rejected by guardrail; falling back to curated script for '%s'.", tid)
                    payload = self.script_engine._finalize(pkg, topic, origin="curated")
                    payload["human_reviewed"] = reviewed
                    from src.script_engine import curated_human_review
                    review = curated_human_review(tid) if reviewed else None
                    if review:   # who reviewed it and when, signed apart; never counted as a fact check
                        payload["human_review"] = review
                    passed, payload, notes = self.script_engine.verify_claims(payload, topic=topic, allow_unreviewed_curated=dry_run)

        # Soft check on the hook's form: a passed draft that opens in another form than asked is revised once,
        # its first sentence only, and the revision is kept only if it passes the fact checks again and is in
        # the asked form. Accuracy outranks the form: otherwise the passed draft stands. It runs before the
        # share line, the credits and the guardrail record, so an accepted revision gets all of them as any
        # passed draft does (PR review, 2026-11-02: it lost guardrail_passed and failed the fact record).
        written = hook_learning.classify(payload.get("hook_sentence", ""))
        revised = None
        if (passed and hook_form and written and written != hook_form
                and payload.get("origin") in ("claude", "gemini-free")):
            revised = False
            try:
                retry = self.script_engine.generate_script(
                    topic, revision_notes=hook_learning.revision_note(hook_form, payload.get("hook_sentence", "")),
                    previous=payload)
                r_passed, retry, r_notes = self.script_engine.verify_claims(retry, topic=topic,
                                                                           allow_unreviewed_curated=dry_run)
                if r_passed and hook_learning.classify(retry.get("hook_sentence", "")) == hook_form:
                    payload, notes, written, revised = retry, r_notes, hook_form, True
                    drafts += 1
            except Exception as e:  # noqa: BLE001 - the passed draft stands whatever the revision does
                log.warning("Hook form revision not used (%s); the passed draft stands.", e)
        if passed:
            # Meta's creator tips (2026-10-25): ask viewers to share, invite them to follow. One line, the
            # writer's when it passes its checks, before the hashtags of both captions; the same line closes
            # the video and the carousel. Placed before the fact review is recorded, so it is part of the copy.
            from src.script_engine import share_prompt, with_share_line
            share = share_prompt(payload)
            if payload.get("caption"):
                payload["caption"] = with_share_line(payload["caption"], share)
            if payload.get("carousel_caption"):
                payload["carousel_caption"] = with_share_line(payload["carousel_caption"], share)
            credit = caption_credit_line(load_credits(ASSETS_DIR / tid))
            if credit:  # CC BY images must be attributed where the work is shared
                # The credit line replaces any earlier one (beat_images.credit_caption). A revision of a verified
                # script (the line-picture rewording) starts from a caption that already carries it: appended
                # again, the two blocks took an unchanged caption past 2,200 characters and the rewording of
                # three narration lines was refused for it (production Derinkuyu, 2026-10-29).
                payload["caption"] = beat_images.credit_caption(payload["caption"], {"images": []}, [])
                payload["caption"] = f"{payload['caption']}\n\n{credit}"
                if len(payload["caption"]) > 2200:
                    passed, notes = False, "Caption plus image credits exceeds Instagram's 2,200 characters."
        payload["guardrail_passed"] = passed
        # How many drafts it took and why: a run of more than two is a finding for the improvement ledger.
        # Every draft the writer produced, not only the fact-check rounds (the record read "1 round" for a run
        # whose log showed two drafts rejected inside the engine: Memnon, 2026-10-25).
        payload["script_rounds"] = max(drafts, revision + 1)
        payload["fact_rounds"] = revision + 1 + (1 if revised else 0)
        payload["script_round_reasons"] = [r[:200] for r in structural + earlier]
        payload["hook_plan"] = {"asked": hook_form, "written": written, "revised": revised, "why": hook_why}
        payload["engagement_plan"] = {
            "asked": list(asks) if asks else None, "why": asks_why,
            "written": [engagement_learning.classify_share(payload.get("share_line", "")),
                        engagement_learning.classify_question(
                            engagement_learning.caption_question(payload.get("caption", "")))]}
        from src.fact_evidence import current_fact_review, unchecked_pass
        if passed and not current_fact_review(payload):
            # Operator decision 2026-11-04: a stamp exists only where the automated checkers ran and passed on this
            # copy. No script-stage pass is one (a human-reviewed curated script, an unreviewed one in a dry run,
            # or a draft checked before its share line and credits were added), so nothing is stamped here: the
            # final copy goes through trace_check and evidence_check (_verify_final_copy), the same path for every
            # script, and is stamped there with their signed record.
            payload.pop("fact_review", None)
            log.info("Script passed its stage%s; its final copy is fact-checked before any fact review is recorded.",
                     " without a fact checker (unreviewed curated, dry run)" if unchecked_pass(payload)
                     else " (human reviewed: the review is recorded, not counted as a fact check)"
                     if payload.get("human_reviewed") else "")
        (run_dir / "script_metadata.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        self.db.update_run(run_id, "producing", script_payload=payload)
        if not passed:
            raise RuntimeError(f"Script rejected by guardrail: {notes}")
        return payload

    def _verify_final_copy(self, payload, topic):
        from src.fact_evidence import current_fact_review
        from src import trace_check, config as pipeline_config
        if current_fact_review(payload):
            return
        # production with no FACT_RECORD_KEY stops before a checker call: nothing could be signed (decision A)
        from src.fact_evidence import require_signing_key
        require_signing_key()
        sheet_path = pipeline_config.DATA_DIR / "fact_sheets" / f"{topic['id']}.json"
        if not sheet_path.is_file():
            raise ValueError("Final package verification requires a fact sheet")
        sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
        # the models that ANSWERED go on the gate record, not the configured ones
        from src.llm import watch_served
        with watch_served() as t_served:
            passed, _, audits, notes = trace_check.check(payload, sheet)
        payload["guardrail_passed"] = passed
        payload["final_fact_audits"] = audits
        if not passed:
            raise ValueError("Final public copy failed fact verification: " + notes)
        with watch_served() as e_served:
            status, confidence, evidence_audits, evidence_notes = trace_check.evidence_check(payload, sheet)
        payload["second_check"] = {"status": status, "confidence": confidence,
                                    "audits": evidence_audits, "summary": evidence_notes}
        if status != "pass":
            payload["guardrail_passed"] = False
            raise ValueError("Final public copy failed evidence verification: " + evidence_notes)
        from src.fact_evidence import stamp_checked_review
        from src.record_signing import key_problem
        models = trace_check.checker_models()
        served = {"trace_check": t_served, "evidence_check": e_served}
        models = {k: " + ".join(dict.fromkeys(served[k])) if served[k] else v for k, v in models.items()}
        if key_problem():
            # development with no FACT_RECORD_KEY (production stopped above): no record can be signed, so none is
            # stamped; the release gate says why and nothing publishes, as with an unsigned record before
            payload.pop("fact_review", None)
            return
        # each run signed as the checkers returned it (FACT_RECORD_KEY), then the gate record and the stamp
        stamp_checked_review(payload, sheet_path, (passed, None, audits, notes),
                             (status, confidence, evidence_audits, evidence_notes), models)

    def _lines_against_pictures(self, topic, run_id, run_dir, dry_run, payload, scene_media, checkpoints,
                                kept_words=False) -> Dict[str, Any]:
        """Each verified sentence against the descriptions of the photos the video can use, before the paid voice
        (src/prevoice_lines.py; production Roman concrete x3, 2026-10-26: "modern concrete / infrastructure" was
        voiced with no licensed photo of it, so after the render it could only be flagged). The concrete sentences
        no photo shows are sent back to the writer once, through the same revision as a fact-check rewrite (the
        writer, both fact checks, length and structure again); a rewording that fails, or still names something
        no photo shows, leaves the verified script as it was, said on the card. Returns the payload to voice,
        stamped with its verdict (a new dict when stamped). One light text-only call per new (script, photos);
        none for a kept or already-voiced script, or one already judged against these photos (resume, re-render,
        a library script that was judged). Never raises, except a cancel."""
        from src import improve, prevoice_lines as pl
        # A regeneration whose reason is the pictures keeps its script, but a line no picture can show may be
        # reworded, minimally (production Derinkuyu re-render, 2026-10-29: "Pictures" kept the script word for word,
        # so the three lines no photo showed went on screen over unrelated photos again). Only those lines change,
        # through the same writer and both fact checks; every other line is kept word for word or the rewording is
        # refused; a reworded script is voiced again (its old take is for other words).
        for_pictures = bool(kept_words) and pl.rewords_for(self._feedback())
        if not pl.enabled() or (kept_words and not for_pictures) or not payload.get("narration_script"):
            return payload
        from src.audio_engine import voiceover_file
        if voiceover_file(run_dir).is_file() and (run_dir / "words.json").is_file():
            return payload  # a take this run already paid for is never thrown away by this check
        try:  # a regeneration resumed before its voice was copied still keeps the words (review, 2026-10-28)
            previous = self._previous_run_dir()
            if not for_pictures and previous is not None and voiceover_file(previous).is_file() and json.loads(
                    (previous / "script_metadata.json").read_text(encoding="utf-8")).get("narration_script") == payload["narration_script"]:
                return payload  # the previous run's paid take is for these exact words: never reworded here
        except (OSError, ValueError, AttributeError):
            pass
        photos = pl.pool_descriptions(self.visual_engine.assets_dir / topic["id"], scene_media)
        if not photos:
            return payload
        # The photos this run's picture side already found for a line are part of the pool (a resume judges the
        # pool it judged before, and pays nothing again).
        side = pl.load_side(run_dir)
        known = {p["file"] for p in photos}
        photos += [p for p in side.get("photos") or [] if isinstance(p, dict) and p.get("file") not in known]
        key = pl.keys(payload["narration_script"], photos)
        stamp = payload.get(pl.STAGE) or {}
        # The kept script's stamp is the previous run's verdict, which left its lines unshown: a pictures regeneration
        # asks again (this run's own checkpoint still holds on a resume). So does a new run of a script whose stamp
        # left lines unshown (a library script whose rewording was refused, production Derinkuyu 2026-10-29, by a
        # caption bug since fixed): another run tries the rewording once more, a resume of this one does not.
        stamped = all(stamp.get(k) == v for k, v in key.items()) and not stamp.get("left")
        if (stamped and not for_pictures) or checkpoints.reusable(pl.STAGE, key):
            log.info("Line-picture check before the voice: already judged for this script and these photos.")
            return payload
        title = topic.get("title", run_id)
        self._check_cancel(run_id)
        self._progress_note(run_id, title, 1, "Checking each line against the photos before the voice")
        verdicts = pl.judge(payload["narration_script"], photos, title)
        if verdicts is None:  # not stamped: asked again on the next run of this script
            return payload
        # Only a line naming a particular thing goes back (prevoice_lines.needs_rewrite); an unshown kind-of-thing line
        # is left to the render's fallback cascade (src/picture_coverage.py).
        # With the payload, protected "marvel" lines the fact check supports with a source are kept from the writer and
        # routed to the render's cascade (illustration panel, 2026-11-02).
        missing = pl.needs_rewrite(verdicts, payload)
        side_report: Dict[str, Any] = {}
        if missing:  # the picture side first: the reserve photos, then a bounded search for just these lines
            missing, verdicts, photos, side_report = self._picture_side(topic, run_id, run_dir, payload["narration_script"],
                                                                        verdicts, missing, photos)
            missing = pl.needs_rewrite(verdicts, payload) if missing else missing
        protected = pl.protected_sentences(payload)
        final, final_verdicts, rewritten, left, why_left = payload, verdicts, [], [], ""
        if missing and for_pictures:
            # QF-14: a kept script written before the hook rule may open over the cap or on a label (blocking faults).
            # Its rewording would be refused at its opening and rewritten, and a pictures regeneration refuses any
            # change outside the asked lines: every call would be spent on a result that is always refused. Unless the
            # first sentence is itself an asked line, its words are kept and no call is made. A weak signal alone does
            # not block (operator ruling 2026-11-10): that script is reworded as before, and its opening is not
            # edited again (script_engine._edit_story, once per opening).
            # QF-15: likewise a kept script with a blocking payoff fault (every script written before the rule has no
            # payoff_sentence): its edit would change the penultimate sentence, unless that is itself an asked line. A
            # payoff that only names no opening noun is reworded as before and not edited again (once per payoff).
            # QF-16: likewise a kept script whose opening, payoff or loop states a disputed fact, unless that
            # sentence is itself an asked line.
            from src import fact_sheet
            from src.script_engine import (
                _close_sentences,
                _first_sentence,
                _split_sentences,
                blocking_problems,
                hedge_problems,
                hook_problems,
                payoff_problems,
            )
            narration, asked = payload["narration_script"], [v["sentence"] for v in missing]
            penultimate = (_split_sentences(narration)[-2:-1] or [""])[0]
            hedge_sheet = (fact_sheet.load(payload.get("topic_id") or topic.get("id") or "", max_age_days=3650)
                           if payload.get("fact_sheet") else None)
            hedged = blocking_problems(hedge_problems(narration, hedge_sheet))
            if (blocking_problems(hook_problems(narration)) and _first_sentence(narration) not in asked):
                left, missing = missing, []
                why_left = "the kept script's opening fails the hook rule's cap or label; a rewording would be refused"
                log.info("Line-picture check: the kept script stands (%s).", why_left)
            elif (blocking_problems(payoff_problems(narration, payload.get("payoff_sentence") or ""))
                  and penultimate not in asked):
                left, missing = missing, []
                why_left = "the kept script has no payoff the payoff rule accepts; a rewording would be refused"
                log.info("Line-picture check: the kept script stands (%s).", why_left)
            elif hedged and not any(f"the {w} (" in p and x in asked for p in hedged for w, x in _close_sentences(narration)):
                left, missing = missing, []
                why_left = "the kept script states a disputed fact in its opening, payoff or loop; a rewording would be refused"
                log.info("Line-picture check: the kept script stands (%s).", why_left)
        if missing:
            log.info("Before the voice, %d line(s) name something no photo shows; sent back to the writer (at most %d "
                     "round(s)): %s", len(missing), pl.rounds(), " | ".join(v["sentence"] for v in missing)[:300])
            import copy
            revised = None
            # Up to pl.rounds() rewordings (2026-11-02): each from the last one that passed its checks, for the lines
            # it still left unshown; a further round only while the previous one left fewer such lines. Every round
            # goes through the writer and both fact checks; only a rewording that leaves no such line is voiced.
            current, asked = payload, missing
            for round_no in range(1, pl.rounds() + 1):
                revised = None
                draft = copy.deepcopy(current)
                # The writer revises the copy as it wrote it: the image credit block is added again after its checks,
                # never twice (production Derinkuyu, 2026-10-29).
                draft["caption"] = beat_images.credit_caption(draft.get("caption") or "", {"images": []}, [])
                try:
                    revised = self._write_and_check_script(topic, run_id, run_dir, dry_run, initial_payload=draft,
                                                           initial_notes=pl.revision_note(asked, photos, minimal=for_pictures))
                    from src.fact_evidence import current_fact_review
                    if not current_fact_review(revised):
                        self._verify_final_copy(revised, topic)
                except JobCancelled:
                    raise
                except Exception as e:  # never lose a verified script: the rewording is a saving, not a new way to fail
                    revised, why_left = None, f"the rewording did not pass its checks: {pl.clip(str(e), 120)}"
                if revised is not None and revised.get("origin") != payload.get("origin"):
                    # The revision fell back to another script (the curated one): not a rewording of these lines, so
                    # the card would name sentences it never changed (review, 2026-10-28)
                    revised, why_left = None, "the rewording fell back to another script"
                if revised is not None and for_pictures:
                    changed = pl.other_lines_changed(payload["narration_script"], revised["narration_script"],
                                                     [v["sentence"] for v in missing])
                    if changed:   # a pictures regeneration keeps every other line word for word, or nothing changes
                        revised, why_left = None, f"the rewording changed other lines too (“{pl.clip(changed[0], 60)}”)"
                if revised is not None:
                    lost = pl.protected_rewrite_problem(payload["narration_script"], revised["narration_script"], protected)
                    if lost:  # a protected line keeps its numbers and names, or the rewording is refused
                        revised, why_left = None, lost
                if revised is None:
                    break
                again = pl.judge(revised["narration_script"], photos, title)
                now = pl.sentences(revised["narration_script"])
                still = pl.needs_rewrite(again, revised) if again is not None else [v for v in asked if v["sentence"] in now]
                if not still:
                    final, final_verdicts, rewritten = revised, again or [], missing
                    break
                closer, why_left, revised = revised, "the rewording still names something no photo shows", None
                if round_no >= pl.rounds() or len(still) >= len(asked) or again is None:
                    break
                log.info("Before the voice, round %d left %d line(s) no photo shows; asking once more.", round_no, len(still))
                current, asked = closer, still
            if revised is None:
                left = missing
                log.info("Line-picture check: the verified script stands (%s).", why_left)
                try:  # the failed rewording wrote its own payload; the run's record is the verified script again
                    (run_dir / "script_metadata.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                                                 encoding="utf-8")
                    self.db.update_run(run_id, "producing", script_payload=payload)
                except Exception as e:
                    log.debug("Script record not restored: %s", e)
        for text in pl.findings(rewritten, left, why_left):
            improve.note("video.picture_relevance", text, source="prevoice_lines")
        done = pl.keys(final["narration_script"], photos)
        out = dict(final, **{pl.STAGE: {**done, "unshown": [v["sentence"] for v in pl.unshown(final_verdicts)],
                                        "rewritten": [v["sentence"] for v in rewritten],
                                        "left": [v["sentence"] for v in left],
                                        **({"pictured": side_report["covered"],
                                            "writer_calls_saved": side_report["writer_calls_saved"]}
                                           if side_report else {}),
                                        # why a kept script was looked at again, and that its rewording is the only change
                                        **({"regeneration": pl.PICTURES_REASON} if for_pictures else {})}})
        card = pl.note(rewritten, left, why_left, pictured=side_report.get("covered"),
                       regeneration=pl.PICTURES_REASON if for_pictures else "")
        if card:
            out["picture_lines_note"] = card
        else:
            out.pop("picture_lines_note", None)
        try:
            record = run_dir / pl.RECORD_FILE
            record.write_text(json.dumps({**out[pl.STAGE], "verdicts": final_verdicts, "photos": photos}, indent=1,
                                         ensure_ascii=False), encoding="utf-8")
            checkpoints.complete(pl.STAGE, done, [record])
        except (OSError, ValueError) as e:
            log.debug("Line-picture verdict not recorded: %s", e)
        return out

    def _picture_side(self, topic, run_id, run_dir, narration, verdicts, missing, photos):
        """Before a line no photo shows goes back to the writer (production Derinkuyu, 2026-10-29: four such lines
        cost a writer call, both fact checks again and a length edit, 20 minutes, and the rewording was refused),
        the pictures are looked for: the pool's reserve context shots (their descriptions; no call), then a Commons
        search for just those lines (at most pl.GAP_LINES lines, beat_images.PER_BEAT photos each, through the
        same licence, relevance, text and look screens as the render's repair search). After each, the lines are
        judged again against the larger pool (one light text-only call). Returns (the lines still unshown, the
        verdicts, the pool, a report); a photo that shows a line is recorded in run_dir/pl.SIDE_FILE for the
        render, which checks it against its line again. Never raises, except a cancel: on any failure the lines
        go to the writer as before."""
        from src import prevoice_lines as pl
        from src.credits import load_credits
        title, first, started = topic.get("title", run_id), len(photos), time.monotonic()
        topic_dir = self.visual_engine.assets_dir / topic["id"]
        pool, reserve_files, found = list(photos), set(), {}

        def judged_again(lines):
            again = pl.judge(" ".join(v["sentence"] for v in lines), pool, title)
            if again is None:
                return lines, None
            return pl.needs_rewrite(again), again

        merged = {v["sentence"]: v for v in verdicts}
        still = missing
        try:
            try:
                turned = getattr(self, "_still_turned", set()) or set()
                reserve = [p for p in self.visual_engine.reserve_sequence(topic) if p.name not in turned]
            except Exception:
                reserve = []
            more = [d for d in pl.pool_descriptions(topic_dir, reserve) if d["file"] not in {p["file"] for p in pool}]
            if more:
                pool += more
                reserve_files = {d["file"] for d in more}
                still, again = judged_again(still)
                merged.update({v["sentence"]: v for v in again or []})
            if still:
                self._check_cancel(run_id)
                self._progress_note(run_id, title, 1, "Looking for photos of the lines no photo shows")
                lines = pl.sentences(narration)
                wanted = [lines.index(v["sentence"]) + 1 for v in still if v["sentence"] in lines][:pl.GAP_LINES]
                try:
                    seen = {r.get("title") for r in load_credits(topic_dir).get("images", []) if isinstance(r, dict)}
                except (OSError, ValueError):
                    seen = set()
                _, credits = beat_images.source_gaps(
                    [{"text": s} for s in lines], wanted, topic, run_dir / "beat_images", seen=seen,
                    notes="\n".join(f"Beat {lines.index(v['sentence']) + 1}: no photo shows it ({v.get('reason', '')})"
                                    for v in still if v["sentence"] in lines), prefix=pl.GAP_PREFIX,
                    deadline=started + pl.SEARCH_SECONDS)   # the run's whole picture side stays within its budget
                for cred in credits:
                    d = pl.found_description(cred)
                    if d["file"] and d["file"] not in {p["file"] for p in pool}:
                        pool.append(d)
                        found[d["file"]] = dict(cred, line=lines[cred["beat"] - 1] if 0 < cred.get("beat", 0) <= len(lines) else "")
                if found:
                    still, again = judged_again(still)
                    merged.update({v["sentence"]: v for v in again or []})
        except JobCancelled:
            raise
        except Exception as e:  # the picture side is a saving, never a new way for a run to fail
            log.warning("Picture side before the voice could not run (%s); the lines go to the writer.", e)
            return missing, verdicts, photos, {}
        final_verdicts = [merged.get(v["sentence"], v) for v in verdicts]
        names = pl.cited(final_verdicts, pool, first)
        kept = [p for p in pool[first:] if p["file"] in names]
        covered = [v["sentence"] for v in missing if v["sentence"] not in {s["sentence"] for s in still}]
        report = {"covered": covered, "writer_calls_saved": 1 if covered and not still else 0,
                  "reserve": sorted(reserve_files & set(names)), "searched": sorted(found)}
        if kept:
            side = pl.load_side(run_dir)
            had = {p.get("file") for p in side.get("photos") or []}
            record = {"photos": (side.get("photos") or []) + [p for p in kept if p["file"] not in had],
                      "reserve": sorted(set(side.get("reserve") or []) | (reserve_files & set(names))),
                      "found": (side.get("found") or []) + [found[n] for n in names if n in found and n not in had]}
            try:
                (run_dir / pl.SIDE_FILE).write_text(json.dumps(record, indent=1, ensure_ascii=False, default=str),
                                                     encoding="utf-8")
            except OSError as e:
                log.debug("Picture-side record not written: %s", e)
        # The pool the verdicts number: the photos given, then the ones that show a line, in the record's order.
        pool_out = photos + kept
        renumber = {p["file"]: i for i, p in enumerate(pool_out, 1)}
        for v in final_verdicts:
            n = v.get("photo")
            if isinstance(n, int) and 0 < n <= len(pool):
                v["photo"] = renumber.get(pool[n - 1]["file"])
        log.info("Picture side before the voice: %d of %d line(s) now have a photo (%d reserve, %d found by a search "
                 "of %d); %s.", len(covered), len(missing), len(report["reserve"]),
                 len([n for n in names if n in found]), len(found),
                 "no writer call needed" if report["writer_calls_saved"] else
                 f"{len(still)} line(s) still go to the writer")
        return still, final_verdicts, pool_out, report

    def _fit_pictures(self, run_id, topic, beats, scenes, durations, candidates, beat_credits, where=None,
                      file_findings=True):
        """Each photo beat's picture against its line before the render (src/scene_planner.fit_to_lines): a picture
        that does not show it is swapped for one that does, else our matching graphic, else the previous shot is
        held; a line nothing shows is filed (video.picture_relevance, unless `file_findings` is off: the caller
        files them after the hold) and said on the card. `where` (original beat -> beat of `beats`, 0-based) when
        `beats` are a held video's: the found photos' lines are in the narration's numbering. Never raises, except
        a cancel: the plan stays as it was when the check fails."""
        from src import improve
        title = topic.get("title", run_id)
        self._progress_note(run_id, title, 3, "Synthesizing graphics, stills & motion · checking each picture against its line")
        to_beat = (lambda b: where[b - 1] + 1 if isinstance(b, int) and 0 < b <= len(where) else b) if where else (lambda b: b)
        line_beat = {Path(c["upscaled"]): to_beat(c.get("beat")) for c in beat_credits or [] if c.get("upscaled")}
        try:
            from src.config import OUTPUT_DIR
            run_dir = OUTPUT_DIR / run_id
            # The reserve photos, offered where the previous shot would otherwise be held over a second line.
            reserve = [Path(r) for r in getattr(self, "_fit_reserve", None) or []]
            fitted, report = fit_to_lines(beats, scenes, candidates, title, durations=durations, line_beat=line_beat,
                                          cancel=lambda: self._check_cancel(run_id),
                                          topic=topic, work_dir=run_dir,
                                          **({"reserve": reserve} if reserve else {}))
        except JobCancelled:
            raise
        except Exception as e:  # the check is a saving, never a new way for a run to fail
            log.warning("Picture-line check could not run (%s); the plan is kept.", e)
            return list(scenes), {"judged": 0, "cached": 0, "swapped": [], "graphic": [], "held": [], "unshown": [],
                                  "stopped": f"error: {e}"}
        if file_findings:
            for text in fit_findings(report):
                improve.note("video.picture_relevance", text, source="picture_fit")
        if report.get("judged") or report.get("cached"):
            log.info("Picture-line check: %d new look(s), %d cached; %d swapped, %d graphic, %d held, %d with no picture%s.",
                     report["judged"], report["cached"], len(report["swapped"]), len(report["graphic"]),
                     len(report["held"]), len(report["unshown"]), f" ({report['stopped']})" if report["stopped"] else "")
        return fitted, report

    def _picture_side_media(self, topic, run_dir, beats, beat_credits):
        """(reserve stills, found photos' credit records) the check before the voice found for a line
        (prevoice_lines.SIDE_FILE), prepared like the render's own (text and edge screen, upscale, faces); a found
        photo is filed under the beat that says its line. ([], []) when none; never raises."""
        from src import prevoice_lines as pl
        side = pl.load_side(run_dir)
        if not side:
            return [], []
        try:
            topic_dir = self.visual_engine.assets_dir / topic["id"]
            turned = getattr(self, "_still_turned", set()) or set()
            stills = [topic_dir / n for n in side.get("reserve") or [] if (topic_dir / n).is_file() and n not in turned]
            if stills:
                stills = beat_images.without_burned_in_text(stills, topic.get("title", ""), 0)
                stills = [Path(p) for p in privacy.anonymize_all([prepared_still(p) for p in stills])]
            have = {c.get("title") for c in beat_credits}
            found = [dict(c) for c in side.get("found") or [] if isinstance(c, dict) and c.get("file")
                     and Path(c["file"]).is_file() and c.get("title") not in have]
            for cred, safe in zip(found, privacy.anonymize_all([prepared_still(Path(c["file"])) for c in found])):
                cred["upscaled"] = str(safe)
                cred["beat"] = beat_of_line(beats, cred.get("line") or "") or cred.get("beat")
            if stills or found:
                log.info("Pictures: %d photo(s) found before the voice for its lines (%d reserve, %d searched).",
                         len(stills) + len(found), len(stills), len(found))
            return stills, found
        except Exception as e:  # a saving, never a new way for a run to fail
            log.warning("Photos found before the voice not offered (%s).", e)
            return [], []

    def _reserve_stills(self, topic, beats, candidates) -> List[Path]:
        """The pool's context shots beyond the usual cap, prepared like the others (text and edge screen, upscale,
        faces), as many as the photo beats the candidates' distinct stills cannot cover (still_shortfall): offered
        only where a picture would otherwise repeat (scene_planner.no_repeats). Never raises: none on a failure."""
        from src.scene_planner import still_shortfall
        try:
            short = still_shortfall(len(beats), candidates)
            if short <= 0:
                return []
            turned = getattr(self, "_still_turned", set())
            offered = {Path(c).stem for c in candidates}  # one found before the voice is offered already
            reserve = [p for p in self.visual_engine.reserve_sequence(topic) if p.name not in turned
                       and not any(o.startswith(p.stem) for o in offered)][:short]
            if not reserve:
                return []
            reserve = beat_images.without_burned_in_text(reserve, topic.get("title", ""), 0)
            reserve = privacy.anonymize_all([prepared_still(p) for p in reserve])
            log.info("Pictures: %d photo beat(s) short; %d context photo(s) held in reserve against a repeat: %s.",
                     short, len(reserve), ", ".join(Path(p).name for p in reserve))
            return [Path(p) for p in reserve]
        except Exception as e:  # the reserve is a saving, never a new way for a run to fail
            log.warning("Reserve photos not prepared (%s); a short pool repeats or holds pictures instead.", e)
            return []

    HELD_CHECK_CALLS = 8   # new looks a run's held shots may ask for (a cached verdict costs nothing)
    _held_looks: Optional[int] = None   # what is left of it this run (reset where the render stage starts)

    def _hold_short_pool(self, run_id, topic, beats, scenes, durations, fit_report, available, storyboard=False):
        """A picture held over two lines instead of cut back in later (scene_planner.hold_for_short_pool): a line
        takes a neighbour's different picture only when the light model judges that picture right for it (the fit
        check's cached verdicts first; at most HELD_CHECK_CALLS new looks a run, none once a look fails or the fit
        check found the model down). An approved storyboard is not changed (its repeats are said). The fit check's
        findings are filed here, for the lines still unshown after the hold. Returns (beats, scenes, durations,
        fit_report, hold_report, where) for the video from here on, the fit report in its numbering. Never raises,
        except a cancel: the plan stays as it was on a failure."""
        from src import improve
        from src.credits import sha256_file
        from src.scene_planner import hold_for_short_pool, remap_beats
        same = (list(beats), list(scenes), list(durations), fit_report,
                {"merged": [], "shown": [], "repeats": [], "distinct": 0, "still_beats": 0, "short": False},
                list(range(len(beats))))
        try:
            fits = {b - 1: True for b in fit_report.get("fits") or []}
            fits.update({u["beat"] - 1: False for u in fit_report.get("unshown") or [] if isinstance(u.get("beat"), int)})
            cache = beat_images.load_fit_cache()
            subject = topic.get("title", topic.get("id", ""))
            down = str(fit_report.get("stopped") or "")
            if self._held_looks is None:
                self._held_looks = self.HELD_CHECK_CALLS
            spent, digests = [False], {}

            def check(picture, line):
                if not config.PICTURE_VISION_CHECK or down.startswith(("the picture check could not run", "error")):
                    return None
                if str(picture) not in digests:   # one hash per photo, whatever the lines asked about it
                    digests[str(picture)] = sha256_file(Path(picture))
                digest = digests[str(picture)]
                if beat_images.fit_key(digest, line) not in cache:
                    if self._held_looks <= 0:
                        return None
                    self._check_cancel(run_id)
                    self._held_looks -= 1
                    spent[0] = True
                verdict = beat_images.line_fit(Path(picture), line, subject, cache, digest=digest)
                if verdict is None:   # the model is down: every further look would wait out its retry
                    self._held_looks = 0
                    return None
                return verdict["fits"]

            try:
                beats, scenes, durations, where, held = hold_for_short_pool(beats, scenes, durations, fits=fits,
                                                                             check=check, available=available,
                                                                             merge=not storyboard)
            finally:
                if spent[0]:
                    beat_images.save_fit_cache(cache)
            if held["merged"]:
                fit_report = remap_beats(fit_report, where, scenes, held["shown"])
            result = (beats, scenes, durations, fit_report, held, where)
        except JobCancelled:
            raise
        except Exception as e:  # the hold is a saving, never a new way for a run to fail
            log.warning("Held shots could not be planned (%s); the plan is kept.", e)
            result = same
        for text in fit_findings(result[3]):
            improve.note("video.picture_relevance", text, source="picture_fit")
        return result

    def _render_approve_publish(self, topic, run_id, run_dir, payload, scene_media, hero_img,
                                vo_path, bed_path, words, vo_dur, auto_approve, dry_run, previous_status,
                                prepared=None, bed_for_cuts=None):
        tid = topic["id"]
        # 6. Source quality: upscale stills too small for a sharp 1080x1920 crop (local Real-ESRGAN),
        # then de-identify living human faces before rendering. Usually already done in the
        # background while the music bed was made (`prepared`).
        timer, t0 = StageTimer(run_dir), time.perf_counter()
        if prepared is not None:
            scene_media, hero_img, low_res = prepared.result()
            timer.record("image_prep_wait", time.perf_counter() - t0)  # what was left after the bed
        else:
            scene_media, hero_img, low_res = self._prepare_images(scene_media, hero_img, run_dir)
        beats = segment_beats(words, vo_dur)
        if config.VIDEO_HEIGHT > config.VIDEO_WIDTH:
            from src.image_review import required_photos
            from src.scene_planner import picture_id, script_asks_for_graphics
            from src.typography import is_video
            distinct = {picture_id(path) for path in scene_media if not is_video(Path(path))}
            # Photo beats: the narration's beats less the one a graphic takes when the script asks for one.
            photo_beats = [{}] * max(0, len(beats) - (1 if script_asks_for_graphics(payload) else 0))
            if len(distinct) < required_photos(photo_beats) and (ASSETS_DIR / tid / "credits.json").is_file():
                # Fewer distinct photos than photo beats: one bounded refill (asset_pipeline.ensure_pool; not
                # repeated within the refresh period, so after the refill before the voice it only re-reads the
                # pool), and the pool's sequence again when it gained photos, with
                # the same selection as at run start (turned and text-bearing photos out, strong photos for the
                # Stories and carousel, the cover's photograph by hero_photo).
                from src.asset_pipeline import ensure_pool
                from src.image_review import strong_stems
                from src.visual_engine import MissingAssetsError, VisualEngine
                self._check_cancel(run_id)
                ensure_pool(topic, ASSETS_DIR, beats=photo_beats)
                engine = VisualEngine(ASSETS_DIR)
                try:
                    refreshed = [m for m in engine.get_scene_sequence(topic)
                                 if Path(str(m)).name not in getattr(self, "_still_turned", set())]
                except MissingAssetsError:
                    refreshed = []
                if len({picture_id(path) for path in refreshed if not is_video(Path(path))}) > len(distinct):
                    refreshed = beat_images.without_burned_in_text(refreshed, topic.get("title", ""),
                                                                   config.MIN_SCENE_IMAGES)
                    log.info("Photo pool refilled for %d beats: %d distinct photos, was %d.", len(beats),
                             len({picture_id(path) for path in refreshed if not is_video(Path(path))}), len(distinct))
                    self._strong_stems = strong_stems(ASSETS_DIR / tid)
                    scene_media, hero_img, low_res = self._prepare_images(refreshed, engine.hero_image(refreshed), run_dir)
        if low_res:
            payload["source_note"] = (f"🖼 {low_res} of {len(scene_media)} source images were below video resolution "
                                      "and were AI-upscaled; sharper originals would look better.")
            log.info(payload["source_note"])

        # Stills setup
        # The cover style: fixed while cover selection is frozen (src/cover_selector.py, QF-82; a bandit on Reel
        # metrics from 2026-11-01, a per-run rotation before). The renderer fits it to the copy and the hero.
        from src import cover_selector
        style, _style_why = cover_selector.for_run(self.db, run_id, payload, hero=hero_img)

        # 7. Master render
        self._check_cancel(run_id)
        # Match pictures to narration beats; cuts land on the beat boundaries.
        # Kept with the run: the audience's retention curve is read beat by beat later (audience.beat_drops).
        payload["beat_times"] = [{"start": round(float(b["start"]), 2), "text": str(b.get("text") or "")} for b in beats]
        # Extra candidates searched for the concrete subject of individual beats (openly licensed).
        # One query plan (picture subjects per beat) serves both beat images and real footage.
        query_plan = None
        if llm_available():
            try:
                query_plan = beat_images.plan_queries(beats, topic)
            except Exception as e:
                log.warning("Beat query planning failed (%s); no beat images or footage this run.", e)

        from src.checkpoints import Checkpoints, cached_artifacts, file_inputs
        from src.image_presentation import policy_inputs
        from src.typography import FALLBACK_FILES, FONT_FILES, FONTS_DIR
        packaging_inputs = {"presentation": policy_inputs([hero_img, *scene_media]),
                            "photos": file_inputs([hero_img, *scene_media]), "style": style,
                            "strong": sorted(getattr(self, "_strong_stems", None) or []),  # which may face out
                            "picks": file_inputs([Path("src/image_review.py")]),
                            "copy": {key: payload.get(key) for key in ("title", "thumbnail", "story_slides", "carousel_slides", "poll", "share_line")},
                            "rules": file_inputs([Path("src/visual_engine.py"), Path("src/multi_format.py"),
                                                  Path("src/typography.py"), Path("src/config.py"), Path("src/safe_zones.py"),
                                                  Path("src/script_engine.py"), Path("src/still_layout.py")]),
                            "fonts": file_inputs([FONTS_DIR / name for name in (*FONT_FILES.values(),
                                                                                *FALLBACK_FILES.values())])}
        if getattr(self, "progress_msg_id", None):
            try:
                self.telegram_bot.update_progress_card(self.progress_msg_id, 3, 5, topic["title"], run_id, "Synthesizing graphics, stills & motion")
            except Exception:
                pass

        # Concurrently generate thumbnails and Story frames (the carousel is drawn only when it takes a carousel
        # slot: carousel_lazy, QF-81) and source media candidates (beat images, footage, channel graphics, callouts, then/now)
        # Story frames and carousel slides use the topic's own photos only, never a context shot (2026-10-25).
        from src.image_review import only_strong
        strong_media = only_strong(scene_media, getattr(self, "_strong_stems", None))
        encoding.get_video_encoder()  # probe once before parallel renderers race to detect it under load
        t0 = time.perf_counter()
        payload.pop("layout_holds", None)   # QF-23: this render's holds only (a resumed run's old payload had some)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            # each glyph event names what it was drawn on (QF-94)
            from src.font_engine import on_surface
            fut_thumb = pool.submit(cached_artifacts, run_dir, "thumbnails", packaging_inputs,
                                    lambda: on_surface("cover", self.visual_engine.generate_thumbnails, hero_img,
                                                       payload, run_dir, style=style))
            fut_story = pool.submit(cached_artifacts, run_dir, "stories", packaging_inputs,
                                    lambda: self.multiformat_gen.generate_story_sequence(hero_img, payload, run_dir, scene_images=strong_media))
            from src import art_direction
            art = art_direction.record(run_dir, hero_img)  # the topic's accent and grade bias, recorded in palette.json
            payload["art_direction"] = art
            fut_graphics = pool.submit(graphics.make_all, payload, topic, run_dir / "graphics", photo=hero_img, palette=art)
            # The carousel is not drawn here: only the episode that takes a carousel slot draws it, then (QF-81:
            # seven a week were drawn for three slots). The run records the photos it will be drawn from.
            from src import carousel_lazy
            carousel_lazy.plan(run_dir, strong_media, payload)

            fut_beats = pool.submit(beat_images.source, beats, topic, run_dir / "beat_images", plan=query_plan) if query_plan else None
            fut_footage = pool.submit(footage.source, beats, topic, run_dir / "footage", plan=query_plan) if query_plan else None
            fut_callouts = pool.submit(on_surface, "photo callouts",
                                       callouts.make, payload, topic, scene_media, run_dir / "graphics", words=words,
                                       auto_approve=auto_approve)
            fut_then_now = pool.submit(on_surface, "graphic then/now", then_now.make, topic, run_dir / "graphics",
                                       grade_bias=float(art.get("grade_bias") or 0.0))   # the master's bias (QF-86)

            thumb_916, thumb_1x1 = fut_thumb.result()
            # A deck whose checked copy cannot be set whole (typography.LayoutError, QF-23) is held on its own, its
            # frames out of the posting globs and the hold on the review card; the run goes on (hold_layout_error).
            # The carousel is drawn at its slot or for its preview (QF-81), where carousel_lazy.draw holds it the same way.
            story_slides = hold_layout_error(fut_story, run_dir, "stories", payload)
            carousel_slides: list[Path] = []   # drawn at its slot (carousel_lazy.render)

            beat_paths, beat_credits = fut_beats.result() if fut_beats else ([], [])
            footage_clips, footage_credits = fut_footage.result() if fut_footage else ([], [])
            own_graphics = fut_graphics.result()
            labelled = fut_callouts.result()
            then_clip, then_credits = fut_then_now.result()
        timer.record("stills_graphics_sourcing", time.perf_counter() - t0)

        upscaled_beats = [prepared_still(path) for path in beat_paths]
        for cred, safe in zip(beat_credits, privacy.anonymize_all(upscaled_beats)):
            cred["upscaled"] = str(safe)
        # The channel's own animated graphics (map, timeline, scale) as further candidates.
        own_graphics += [g for g in (labelled, then_clip) if g]
        # The photos the check before the voice found for a line (the picture side): offered like the others, and
        # each checked against its line again before the render (_fit_pictures).
        side_stills, side_credits = self._picture_side_media(topic, run_dir, beats, beat_credits)
        beat_credits += side_credits
        candidates = (scene_media + [Path(c["upscaled"]) for c in beat_credits] + own_graphics + footage_clips
                      + side_stills)
        # Fewer distinct pictures than photo beats (Nazca, 2026-10-28): the pool's further context shots, held in
        # reserve against a repeat; what is still short is held over two lines (_hold_short_pool) or said.
        reserve = self._reserve_stills(topic, beats, candidates)
        reserve_arg = {"reserve": reserve} if reserve else {}
        self._fit_reserve = reserve   # the fit check prefers one over holding a shot across two lines
        master_mp4 = run_dir / f"{tid}_master.mp4"
        caption_base, feedback, qa = payload["caption"], "", None
        fb = self._feedback()
        if fb.get("reason") == "pictures":
            feedback = "The human reviewer sent the previous version back for its pictures." + (
                f" Their words: {fb['text']}" if fb.get("text") else "")
        motion_memo: Dict[str, Any] = {}
        # The narration's own beats: every plan starts from them; a hold (_hold_short_pool) joins some for the video
        # it makes, and the video check numbers the beats that video has (`where` maps one to the other).
        from src.typography import is_video
        plan_beats, where = beats, list(range(len(beats)))
        short_noted, self._held_looks = False, None   # the held shots' look budget is the run's
        planned: List[Path] = []   # the first plan's picks, so a placement-only repair can move one
        durations: List[float] = []
        late_graphics: List[Dict[str, Any]] = []
        pinned_beats: Dict[int, Path] = {}
        self._check_cancel(run_id)
        for attempt in (1, 2, 3):  # up to 3 passes: monotonic self-healing converges defective beats
            from src.scene_planner import repair_placement
            repaired = repair_placement(planned, durations, late_graphics) if attempt >= 2 and late_graphics and planned else None
            from src.scene_planner import picture_id
            available = len({picture_id(c) for c in candidates + reserve if not is_video(Path(c))})
            if repaired is not None:   # the video's own beats (the move is within the plan it judged)
                scenes, plan_notes = repaired, "placement repaired in place: " + "; ".join(i.get("issue", "") for i in late_graphics)
                log.info("Scene placement repaired without a second plan: %s", plan_notes[:160])
            else:
                beats = plan_beats
                plan_kwargs = dict(reserve_arg)
                if attempt >= 2 and pinned_beats:
                    import inspect
                    try:
                        sig = inspect.signature(plan_scenes)
                        if "pinned" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                            plan_kwargs["pinned"] = pinned_beats
                    except (ValueError, TypeError):
                        plan_kwargs["pinned"] = pinned_beats
                scenes, durations, plan_notes = plan_scenes(beats, candidates, feedback=feedback, **plan_kwargs)
                log.info("Scene plan: %d beats (%s).", len(beats), plan_notes[:120])
            # Each photo against its own line, before motion, parallax and the render (render audit 2026-10-28).
            scenes, fit_report = self._fit_pictures(run_id, topic, beats, scenes, durations, candidates, beat_credits,
                                                    where=where if repaired is not None else None, file_findings=False)
            from src.scene_planner import STORYBOARD_NOTES
            beats, scenes, durations, fit_report, held, step = self._hold_short_pool(
                run_id, topic, beats, scenes, durations, fit_report, available, storyboard=plan_notes == STORYBOARD_NOTES)
            where = [step[w] for w in where] if repaired is not None else step
            planned = list(scenes)
            used = beat_images.used_credits(scenes, beat_credits)
            used_footage = [c for c in footage_credits if Path(c["file"]) in scenes]
            # Then-and-now photos are Commons images too: credited with the others when that graphic is used.
            used_images = used + (then_credits if then_clip and then_clip in scenes else [])
            caption = beat_images.credit_caption(caption_base, load_credits(ASSETS_DIR / tid), used_images)
            caption = footage.with_credits(caption, used_footage)
            if len(caption) > 2200:  # can't credit them within Instagram's limit, so don't use them
                log.warning("Image/footage credits would overflow the caption; using the topic's images and our graphics only.")
                candidates, used, used_footage, caption = scene_media + own_graphics, [], [], caption_base
                beats = plan_beats    # a fresh plan: from the narration's own beats again
                available = len({picture_id(c) for c in candidates + reserve if not is_video(Path(c))})
                scenes, durations, plan_notes = plan_scenes(beats, candidates, feedback=feedback, **reserve_arg)
                scenes, fit_report = self._fit_pictures(run_id, topic, beats, scenes, durations, candidates, [],
                                                        file_findings=False)
                beats, scenes, durations, fit_report, held, where = self._hold_short_pool(
                    run_id, topic, beats, scenes, durations, fit_report, available, storyboard=plan_notes == STORYBOARD_NOTES)
                planned = list(scenes)  # the plan the repair pass judges (the overflow re-plan replaced the first)
            if config.VIDEO_HEIGHT > config.VIDEO_WIDTH:
                from src.scene_planner import fit_holds, reel_photo_coverage
                from src.visual_engine import MissingAssetsError
                # A still kept past the hold limit only by a pause gets its cut moved into that pause.
                beats, durations, hold_moves = fit_holds(beats, scenes, durations, words)
                coverage_gate = reel_photo_coverage(scenes, durations)
                payload["photo_coverage_gate"] = {**coverage_gate, "cuts_moved": hold_moves}
                if not coverage_gate["ok"]:
                    self.db.update_run(run_id, "producing", script_payload=payload)
                    raise MissingAssetsError("Reel photo coverage: " + "; ".join(coverage_gate["problems"])
                                             + ". Add distinct photos or revise the storyboard before rendering.")
            payload["caption"] = caption
            from src.scene_planner import hold_note, storyboard_opening_note
            reserve_used = sorted({Path(r).name for r in reserve if picture_id(r) in {picture_id(s) for s in scenes}})
            payload["scene_plan_note"] = "\n".join(n for n in (degraded_note(plan_notes), storyboard_opening_note(plan_notes, scenes),
                                                               fit_note(fit_report), hold_note(held, reserve_used, fit_report)) if n)
            payload["picture_hold"] = {**held, "reserve_used": reserve_used}
            if hold_note(held, reserve_used) and not short_noted:  # for the audit ledger, once per run
                short_noted = True
                from src import improve
                improve.note("pictures.short_pool", f"{held['distinct']} distinct pictures for {held['still_beats']} photo "
                             f"beats: {len(held['merged'])} held over two or more lines, {len(held['repeats'])} repeated, "
                             f"{len(reserve_used)} reserve context photo(s) used", source="scene_plan")
            payload["picture_fit"] = {k: fit_report.get(k) for k in ("judged", "cached", "swapped", "graphic", "generated", "held",
                                                                      "unshown", "stopped")}
            # Which cascade tier filled each beat (src/picture_coverage.py), in the run folder for the video check and
            # the operator; a beat a lower tier covered is a warning there, not a blocker (video_qa.coverage_severity).
            from src import picture_coverage
            coverage = picture_coverage.write(run_dir, beats, scenes, fit_report)
            payload["beat_coverage"] = {"file": picture_coverage.COVERAGE_FILE, "summary": coverage["summary"]}
            used_graphics = [p.stem.replace("graphic_", "") for p in own_graphics if p in scenes]
            payload["graphics_note"] = ("🗺 Original graphics in this video: " + ", ".join(used_graphics)) if used_graphics else ""
            generated_list = fit_report.get("generated", [])
            if generated_list:
                # Every illustrated beat on the review card with its narrated line, the illustration and its
                # reference(s) (reliable depiction, 2026-11-02); the run waits for the operator (never auto-approved).
                from src.image_gen import review_note
                payload["reconstruction_note"] = review_note(generated_list)
                from src import improve
                for g in generated_list:
                    improve.note(
                        "visual.archaeological_reconstruction",
                        f"Beat {g['beat']}: synthesized era-matched linework on archival substrate for unphotographed line '{g.get('line', '')[:80]}'",
                        source="generative_gap_filler"
                    )
            payload["footage_credits"] = used_footage
            payload["footage_note"] = (f"🎥 {len(used_footage)} real footage clip(s) "
                                       f"({sum(c['place_specific'] for c in used_footage)} of the actual site): "
                                       + "; ".join(f"{c['provider']}: {c['source_url']}" for c in used_footage)) if used_footage else ""
            # A reference image of a named subject shown as its exact photo (src/scene_planner._show_reference) is
            # credited like any beat image (reliable depiction, 2026-11-02).
            on_screen = {str(Path(p)) for p in scenes}
            used = list(used) + [c for c in fit_report.get("reference_credits") or []
                                 if isinstance(c, dict) and str(Path(str(c.get("file") or ""))) in on_screen]
            payload["beat_image_credits"] = used
            # What each one is and which line it is for: the card said only "check they show the right thing",
            # and a Wells Cathedral clock came into an Antikythera video unnoticed (2026-10-25).
            payload["beat_image_note"] = (
                f"🔍 {len(used)} photo(s) from outside the topic's set, fetched for one line each: "
                + "; ".join(f"line {c.get('beat', '?')}: {clip_words(str(c.get('looked') or c.get('title', '')).removeprefix('File:'), 110)}"
                            for c in used) if used else "")
            payload["beat_image_lines"] = [f"line {c.get('beat', '?')}: {str(c.get('looked') or c.get('title', '')).removeprefix('File:')}"
                                           for c in used]   # whole, for the card's folded list
            left_out = beat_images.unchecked(run_dir / "beat_images")
            if left_out:  # the gate held: said, so a Re-render can try them once the check runs again
                payload["beat_image_note"] = ((payload["beat_image_note"] + "\n") if payload["beat_image_note"] else "") + (
                    f"⚠️ {len(left_out)} photo(s) found for single lines were left out: their picture check could not run "
                    "(the lines use the topic's screened photos). Re-render to try them again.")
            # AI motion for the opening photo (only with a media budget; real footage and our graphics come first).
            self._check_cancel(run_id)
            paid_motion = (not dry_run) or self.paid_motion_in_dry_run or config.MOTION_IN_DRY_RUNS
            scenes, motion_used = motion.apply(self.db, scenes, run_dir / "motion", memo=motion_memo, topic=topic,
                                               allow_paid=paid_motion,
                                               seed_dirs=self._previous_motion_dirs(),  # a regeneration keeps its paid clips
                                               durations=durations)
            payload["motion_note"] = (f"🤖 AI motion ({', '.join(motion_used)}) animates {len(motion_used)} real photo(s); "
                                      "the Reel carries the AI label") if motion_used else ""
            from src import hook_learning
            payload["opening"] = {"shot": hook_learning.opening_shot(scenes, run_dir / "motion")}
            if not motion_used and config.MOTION_MAX_CLIPS > 0:  # a paid provider out of credit: said on the card
                try:
                    payload["motion_note"] = motion.fallback_note(motion_used, paid_motion)
                except Exception as e:
                    log.debug("Motion fallback note not made: %s", e)
            self._check_cancel(run_id)
            # 2.5D parallax clips for the stills (local GPU); falls back to flat camera moves.
            self._progress_note(run_id, topic.get("title", run_id), 3, "Synthesizing graphics, stills & motion · animating the stills")
            t0 = time.perf_counter()
            try:
                scenes = render_parallax(scenes, durations, run_dir / "parallax", TRANS_DUR,
                                         exempt_indices=fit_report.get("macro_drifts", [])) or scenes
            except Exception as e:
                log.warning("Parallax rendering failed (%s); using flat camera moves.", e)
            timer.record(f"parallax_attempt{attempt}", time.perf_counter() - t0)
            t0 = time.perf_counter()
            cached_artifacts(run_dir, "caption_exports", {"words": words, "rules": file_inputs([Path("src/video_composer.py")])},
                lambda: [self.video_composer.export_subtitles_srt(words, master_mp4.with_suffix(".srt")),
                         self.video_composer.export_subtitles_vtt(words, master_mp4.with_suffix(".vtt"))])
            render_checkpoint = Checkpoints(run_dir / "stage_cache" / "render")
            from src.script_engine import end_card_share
            from src.end_card import LOOP_LINE, end_card_note, end_card_record
            # The share line (end_card.LOOP_LINE draws it on the loop bridge; off since 2026-11-03): non-empty, it
            # also tells the composer to end the Reel on the bridge.
            card_share, card_fallback = end_card_share(payload)
            # This 9:16 master's cache keys on the share line only while the bridge draws it (review, 2026-11-03):
            # with LOOP_LINE off a reworded share line changes nothing in the master, only whether a bridge is there.
            # The 16:9 end screen still draws the line and keys on it in its own render.
            card_key = card_share if LOOP_LINE else bool((card_share or "").strip())
            if bed_for_cuts is not None:
                bed_for_cuts(durations)
            render_inputs = {"presentation": policy_inputs(scenes),
                             "media": file_inputs([*scenes, vo_path, bed_path]), "words": words,
                             "durations": durations, "emphasis": payload.get("emphasis_words"), "end_card": card_key,
                             "loop_follow_overlay": bool(config.LOOP_FOLLOW_OVERLAY),   # QF-83: drawn into the master
                             "grade_bias": (payload.get("art_direction") or {}).get("grade_bias") or 0.0,  # part of the master
                             "rules": file_inputs([Path("src/video_composer.py"), Path("src/end_card.py"), Path("src/config.py"),
                                                   Path("src/encoding.py"), Path("src/encoded_audio.py"), Path("src/delivery_profiles.py"),
                                                   Path("src/still_camera.py"), Path("src/camera_motion.py")]),
                             "fonts": packaging_inputs["fonts"]}
            self._check_cancel(run_id)
            if not render_checkpoint.reusable("master", render_inputs):
                self._progress_note(run_id, topic.get("title", run_id), 3, "Synthesizing graphics, stills & motion · rendering the master video")
                self.video_composer.compose_video(scenes, vo_path, bed_path, words, vo_dur, master_mp4, durations=durations,
                                                  emphasis=payload.get("emphasis_words"),
                                                  grade_bias=float((payload.get("art_direction") or {}).get("grade_bias") or 0.0),
                                                  end_card=card_share,
                                                  macro_drifts=fit_report.get("macro_drifts", []))
                outputs = [master_mp4, master_mp4.with_name(master_mp4.stem + "_captions.ass"),
                           master_mp4.with_suffix(".composition.json"), master_mp4.with_suffix(".srt"), master_mp4.with_suffix(".vtt")]
                if all(path.is_file() for path in outputs):
                    render_checkpoint.complete("master", render_inputs, outputs)
            timer.record(f"master_render_attempt{attempt}", time.perf_counter() - t0)
            payload["end_card"] = end_card_record(master_mp4, card_fallback)  # audience analysis compares with and without
            if getattr(self, "progress_msg_id", None):
                try:
                    self.telegram_bot.update_progress_card(self.progress_msg_id, 4, 5, topic["title"], run_id, "Video composited, preparing review")
                except Exception:
                    pass

            # The automated look (an LLM call) and the deterministic audit only read the finished
            # master and cover, so the LLM review runs while the audit measures.
            t0 = time.perf_counter()
            qa_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            fut_qa = qa_pool.submit(review_render, master_mp4, beats, thumb_916, run_dir, scenes)
            # Deterministic quality audit (payload key "self_healing" kept for existing runs)
            try:
                timeline_cuts = []
                t_cursor = 0.0
                for c_idx, (asset, dur) in enumerate(zip(scenes, durations)):
                    b_txt = beats[c_idx]["text"] if c_idx < len(beats) else ""
                    timeline_cuts.append({
                        "scene_index": c_idx,
                        "start": t_cursor,
                        "end": t_cursor + dur,
                        "dur": dur,
                        "text": b_txt,
                        "asset": asset,
                    })
                    t_cursor += dur
                payload["is_generated_by_ai"] = True
                quality_audit = QualityAuditor().certify(
                    video_path=master_mp4,
                    timeline=timeline_cuts,
                    words=words,
                    run_dir=run_dir,
                    carousel_dir=run_dir / "carousel",
                    story_dir=run_dir / "stories",
                    cover_path=thumb_916,
                    grid_path=thumb_1x1,
                    payload=payload,
                )
                payload["self_healing"] = quality_audit
                note = end_card_note(payload.get("end_card"))
                if note:  # shown on the card (info), never a block
                    quality_audit.setdefault("issues", []).append(note)
                # Each line no picture shows, in the card's issue list as "no picture shows: <line>" (info: shown,
                # never a block; operator, 2026-10-29), from the fit check's record of this render's plan.
                from src.scene_planner import no_picture_issues
                quality_audit.setdefault("issues", []).extend(no_picture_issues(payload.get("picture_fit")))
            except Exception as e:
                log.warning("Quality audit could not run: %s", e)

            # Automated look at the finished video; findings go on the review card.
            try:
                qa = fut_qa.result()
            finally:
                qa_pool.shutdown(wait=True)
            timer.record(f"qa_attempt{attempt}", time.perf_counter() - t0)
            # Self-healing: picture mismatches from the video check and the audit's repairable scene
            # defects go back to the scene planner for one corrected render.
            feedback = "\n".join(f for f in (beat_images.picture_issues(qa),
                                             audit_scene_feedback(payload.get("self_healing"))) if f)
            if not feedback or attempt == 3:
                break
            from src.scene_planner import placement_only
            late_graphics = placement_only((payload.get("self_healing") or {}).get("issues"), beat_images.picture_issues(qa))
            payload.setdefault("self_healing_actions", []).append({"attempt": attempt, "fixing": feedback.splitlines()})
            log.info("Repairing before review; re-planning scenes once:\n%s", feedback)
            # Beats whose picture doesn't show what is said get their own Commons search when the first
            # pictures did not cover them (same licence, relevance and privacy steps as the first search).
            gaps = beat_images.mismatched_beats(qa)
            # The re-plan works on the narration's own beats: the findings' beat numbers (the video's) are said in
            # those, and the photos found for a beat are filed under the first line it carried.
            from src.scene_planner import original_beats, renumber_feedback, withdrawals
            feedback = renumber_feedback(feedback, where)
            if gaps:
                gap_paths, gap_credits = beat_images.source_gaps(
                    beats, gaps, topic, run_dir / "beat_images", seen={c.get("title") for c in beat_credits},
                    notes=beat_images.picture_issues(qa))
                for cred, safe in zip(gap_credits, privacy.anonymize_all([prepared_still(p) for p in gap_paths])):
                    cred["upscaled"] = str(safe)
                lines_of = original_beats([c["beat"] for c in gap_credits], where)
                for cred in gap_credits:
                    cred["beat"] = (lines_of.get(cred["beat"]) or [cred["beat"]])[0]
                if gap_credits:
                    beat_credits += gap_credits
                    candidates += [Path(c["upscaled"]) for c in gap_credits]
                    found_for = sorted({c["beat"] for c in gap_credits})
                    feedback += ("\nNew pictures were found for beat(s) " + ", ".join(map(str, found_for))
                                 + ": use them where they show what is said.")
                    payload["self_healing_actions"][-1]["new_pictures"] = [c.get("title") for c in gap_credits]
            # A picture the check found wrong for its line is not offered again: told only "beat 5 shows a
            # car", the re-plan moved the car to beat 6 (Nazca, 2026-10-25). Never for a finding that a picture
            # repeats (it was right where first used: Nazca, 2026-10-28), and only while 3 distinct stills stay,
            # counted with the photos the gap search just found.
            flagged = withdrawals(planned, beat_images.wrong_subject_beats(qa), candidates)
            if flagged:
                candidates = [c for c in candidates if c not in flagged]
                feedback += "\nThe picture(s) flagged above are no longer offered; choose among the others."
                payload["self_healing_actions"][-1]["withdrawn"] = sorted({Path(f).name for f in flagged})
            # Hard-pin verified beats that fit their line, were not flagged by QA, and are not gap beats
            wrong_beats = set(beat_images.wrong_subject_beats(qa)) | set(gaps or [])
            fit_mismatches = set(fit_report.get("mismatches", [])) if hasattr(fit_report, "get") else set()
            disqualified_beats = wrong_beats | fit_mismatches
            if planned:
                pinned_beats = {
                    k: planned[k]
                    for k in range(len(planned))
                    if (k + 1) not in disqualified_beats
                    and planned[k] in candidates
                    and not is_video(planned[k])
                }
                if pinned_beats:
                    log.info("Self-healing: pinned %d verified passing beat(s) for attempt %d: %s",
                             len(pinned_beats), attempt + 1, [f"Beat {k+1}: {p.name}" for k, p in pinned_beats.items()])
        t0 = time.perf_counter()
        self._check_cancel(run_id)
        if getattr(self, "progress_msg_id", None):
            # Every step is reported, so its length is recorded for the next run's estimate (step 5 never
            # was, and the history that needs all five stayed empty).
            try:
                self.telegram_bot.update_progress_card(self.progress_msg_id, 5, 5, topic["title"], run_id,
                                                       "review video, final copy check and release report")
            except Exception:
                pass
        review_mp4 = self.video_composer.make_review_proxy(master_mp4, run_dir / "review_proxy.mp4")
        timer.record("review_proxy", time.perf_counter() - t0)
        payload["qa_summary"] = qa_summary_html(qa, beats)
        # The look's findings in the record, so the release gate reads them (src/release.py): a major picture
        # finding was shown on the card beside "all checks pass" (Nazca, 2026-10-25).
        payload["video_check"] = {"run": qa is not None,
                                  "issues": [{"beat": i.beat, "severity": str(i.severity).lower(), "issue": i.issue,
                                              "where": beat_where(i.beat, beats)}
                                             for i in (qa.issues if qa is not None else [])]}
        # A beat the fallback cascade covered honestly (a same-kind photo, a detail, film, map, graphic or labelled
        # reconstruction) is a warning when the look says only that the very thing named is not shown; a wrong or
        # invented subject still blocks (src/video_qa.coverage_severity, src/picture_coverage.py).
        try:
            from src import picture_coverage
            from src.video_qa import coverage_severity
            payload["video_check"]["issues"] = coverage_severity(payload["video_check"]["issues"],
                                                                 picture_coverage.load(run_dir))
            payload["video_check"]["coverage_warnings"] = sum(
                1 for i in payload["video_check"]["issues"] if isinstance(i, dict) and i.get("warning"))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:  # the findings stand as they were
            log.warning("Picture coverage could not grade the video check (%s); its findings stand.", e)
        (run_dir / "qa_report.json").write_text(qa.model_dump_json(indent=2) if qa else "null", encoding="utf-8")
        log.info("%s", re.sub(r"<[^>]+>", "", payload["qa_summary"]).replace("\n", " | "))
        self._verify_final_copy(payload, topic)
        # Persist the final payload: publishing reads it back (caption incl. music/image credits).
        self.db.update_run(run_id, "pending_approval", master_video_path=master_mp4, script_payload=payload,
                           thumbnail_cover_path=thumb_916, thumbnail_grid_path=thumb_1x1)
        (run_dir / "script_metadata.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        ledger = generate_provenance_ledger(run_dir, topic, payload, master_mp4,
                                   vo_path, bed_path, media_items=scenes,
                                   self_healing=payload.get("self_healing"))
        from src.credits import add_provenance_attributions
        add_provenance_attributions(payload, ledger)
        self.db.update_run(run_id, "pending_approval", script_payload=payload)
        (run_dir / "script_metadata.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        from src.release import ReleaseReportLocked, locked_report, write_release_report
        try:
            release = write_release_report(self.db.get_run(run_id), dry_run=dry_run)
        except ReleaseReportLocked as e:   # held, with the reason on the card; the card re-runs the gate
            log.warning("Release report not saved: %s", e)
            release = locked_report(e)

        # 8-9. Human approval (never implicit). The production card is finished either way.
        self._finish_progress(topic.get("title", tid), run_id, success=True)
        decision = None
        auto_approve = self._auto_approve_allowed(run_id, payload, auto_approve, scenes=scenes)
        blocked = self._blocked_at_gate(run_id, release, dry_run, "auto-approve flag") if auto_approve else None
        if blocked:
            return {"run_id": run_id, "topic_id": tid, "decision": None, "status": "blocked", "error": blocked,
                    "master_video": str(master_mp4)}
        if auto_approve:
            self.db.record_approval(run_id, tid)
            self.db.update_approval_decision(run_id, "approved", decided_by="auto-approve-flag")
            decision = "approved"
        result: Dict[str, Any] = {
            "run_id": run_id, "topic_id": tid, "decision": decision if auto_approve else None,
            "master_video": str(master_mp4), "thumbnails": [str(thumb_916), str(thumb_1x1)],
            "stories": [str(s) for s in story_slides], "carousel": [str(c) for c in carousel_slides],
        }
        # 10. Publish (or book the posting slot when running as the daemon); a decision other than Approve is
        # settled. Under the review lock with the wait itself, so no second wait can act on the same Approve.
        act = (lambda d: self._after_approval(run_id, dry_run=dry_run, previous_status=previous_status)
               if d == "approved" else self._settle_decision(run_id, tid, d))
        if auto_approve:
            decision, acted = self._decide_review(run_id, act, decided=decision)
            if decision is None:  # a wait elsewhere holds this review and acts on the Approve: not twice
                return {**result, "decision": "pending", "status": "pending_approval"}
        else:
            self.telegram_bot.send_approval_request(review_mp4, thumb_916, payload, run_id)
            decision, acted = self._decide_review(run_id, act)
            if decision is None:  # another wait owns this review (it acts on the decision): nothing to do here
                return {**result, "decision": "pending", "status": "pending_approval"}
            result["decision"] = decision
        if decision != "approved":
            result["status"] = decision
            return result
        result.update(acted or {"already_acted": True})
        return result

    def _after_approval(self, run_id: str, dry_run: bool = False, previous_status: str = "queued") -> Dict[str, Any]:
        dry_run = dry_run or self.db.get_setting("run_mode:" + run_id) == "dry_run"
        # The slot is picked from the decision's time, fixed before the backup: the Undo wait and the copy of the run
        # folder never push a last-moment approval past its slot (review 2 of UI-02)
        picked_from = slot_clock(self.db, run_id)
        if getattr(config, "BACKUP_ENABLED", True):
            try:
                from src.backup import backup_run
                run = self.db.get_run(run_id) or {}
                if run.get("master_video_path"):
                    backup_run(Path(run["master_video_path"]).parent, db_path=getattr(self.db, "db_path", None))
            except Exception as e:
                log.warning("Tier 2 Gold Backup skipped: %s", e)
        if self._approve_posting(run_id, dry_run) == "slot":
            return self.schedule_run(run_id, now=picked_from)
        return self.publish_run(run_id, dry_run=dry_run, previous_status=previous_status)

    def _approve_posting(self, run_id: str, dry_run: bool = False) -> str:
        """What Approve does with the run (_after_approval): "dry_run" (a simulated publish, nothing posted),
        "slot" (schedule_run books its Reel a posting slot) or "now" (publish_run posts it at once: no scheduled
        posting, or a documentary, which goes to YouTube at once). The review card says which (QF-43)."""
        if dry_run or self.db.get_setting("run_mode:" + run_id) == "dry_run":
            return "dry_run"
        if self.schedule_posts and POST_SCHEDULING and (self.db.get_run(run_id) or {}).get("content_format") != "deep_dive":
            return "slot"
        return "now"

    # ------------------------------------------------------------ posting slots
    def schedule_run(self, run_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Books an approved run's Reel into the next free slot, its Stories after it, and its
        carousel into the carousel queue. The daemon's post_due() publishes them when due. Under the run's
        booking lock: the check for an existing booking and the booking itself are one step (two interleaved
        calls booked two Reels: review, 2026-10-25)."""
        db_path = getattr(self.db, "db_path", None)
        if db_path is None:
            return self._schedule_run(run_id, now)
        from src.publication_lock import publication_lock
        with publication_lock(db_path, "booking:" + run_id, wait=30):
            return self._schedule_run(run_id, now)

    def _schedule_run(self, run_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        run = self.db.get_run(run_id)
        if not run or not run.get("master_video_path"):
            raise ValueError(f"Run '{run_id}' has no rendered video.")
        booked = [p for p in self.db.list_scheduled_posts(("pending", "published", "unknown"), run_id=run_id) if p["kind"] == "reel"]
        if booked or run.get("status") in ("scheduled", "published"):
            # Booked once only: a second booking would post the Stories and the carousel twice (2026-10-25)
            log.warning("Not booking %s again: it is already %s.", run_id, run.get("status"))
            return {"status": run.get("status") or "scheduled", "already_booked": True,
                    "scheduled_for": booked[0]["due_at"] if booked else None}
        payload = run["script_payload"]
        if payload.get("origin") == "curated" and not payload.get("human_reviewed"):
            from src.release import ReleaseBlocked
            raise ReleaseBlocked("This run used an unreviewed curated script; it can only be published after review.")
        self._require_release(run)
        run_dir = Path(run["master_video_path"]).parent
        taken = self._booked_reel_days()
        slot = self._reel_slot(run_id, now, taken)
        self.db.add_scheduled_post(run_id, "reel", slot.astimezone(timezone.utc).isoformat(), [])
        # A run approved after the slot it was made for is booked later, and says so (it did silently: QF-74)
        worker_deadline.missed_slot_notice(self.db, run, slot, now, taken, self._alert)
        worker_deadline.forget(self.db, run_id)  # decided and booked: its production start has served its purpose
        lines = [f"🎬 Reel: {post_schedule.fmt(slot)}"] + self._book_followers(run_id, run_dir, slot)
        self.db.update_run(run_id, "scheduled")
        self.topic_engine.record_status(run["topic_id"], "scheduled", run_id=run_id)
        # The review card becomes the schedule's receipt (a separate alert said the same thing again).
        if not self.telegram_bot.stamp_review_outcome(run_id, "🗓 <b>Approved · scheduled</b>\n" + "\n".join(html.escape(x) for x in lines)):
            _notice(self, f"🗓 <b>Post scheduled</b>\n\n<b>{html.escape(self.db.run_label(run_id))}</b>\n\n" + "\n".join(html.escape(x) for x in lines), section="info", key=f"approved:{run_id}", run=run_id, replace=True)
        return {"status": "scheduled", "scheduled_for": slot.isoformat()}

    def _book_followers(self, run_id: str, run_dir: Path, reel_slot: datetime) -> List[str]:
        """Books a Reel's Stories (STORY_DELAY_MINUTES after it) and its carousel (its first carousel slot 48 h
        after the Reel, before its deadline: post_schedule.first_carousel_slot, the rule the daemon and Posting use);
        the daemon's post_due() publishes them when due. Shared by slot booking and Post now. reel_slot is the
        Reel's slot, or now when the Reel has posted."""
        lines: List[str] = []
        from src.release import story_frames
        stories = [str(p) for p in story_frames(run_dir)]  # hook and reveal; never a retired poll frame (QF-80)
        if stories:
            when = post_schedule.story_time(reel_slot)
            self.db.add_scheduled_post(run_id, "story", when.astimezone(timezone.utc).isoformat(), stories)
            lines.append(f"📱 {len(stories)} Story frames: {post_schedule.fmt(when)}")
        from src import carousel_lazy
        # A run made from QF-81 on books its carousel undrawn: it is drawn when it takes its slot.
        carousel = [] if carousel_lazy.planned(run_dir) else carousel_lazy.drawn(run_dir)
        if carousel or carousel_lazy.planned(run_dir):
            post_id = self.db.add_scheduled_post(run_id, "carousel", None, carousel, status="waiting")
            post = next(p for p in self.db.list_scheduled_posts(("waiting",), run_id=run_id) if p["id"] == post_id)
            # The slot it will take, as the daemon will see it (it named the next carousel slot, which the 48 h gap
            # and the 7-day expiry could rule out: operator, 2026-11-03); a newer episode's carousel goes first.
            # None only when booked again too late (its Reel posted days ago and its last carousel slot has passed)
            car = post_schedule.first_carousel_slot(post_schedule.carousel_reel(self.db, post)[1], now=reel_slot)
            gap = f"{config.CAROUSEL_GAP_HOURS:g} h"
            lines.append(f"🖼 Carousel: {post_schedule.fmt(car)} (its first carousel slot {gap} after the Reel)" if car else
                         f"🖼 Carousel: its last carousel slot ({config.CAROUSEL_MAX_AGE_DAYS:g} days after the Reel, "
                         f"or its first slot {gap} after it) has passed, so it won't post")
        return lines

    def _reel_slot(self, run_id: str, after: datetime, taken: set) -> datetime:
        """The next free Reel slot after `after` that keeps a sensitive topic a clear audience day from another
        (src/topic_policy.next_safe_slot, QF-73); the plain next free slot for any other topic."""
        from src.topic_policy import next_safe_slot
        return next_safe_slot(self.db, (self.db.get_run(run_id) or {}).get("topic_id") or "", after, taken,
                              run_id=run_id)

    def _booked_reel_days(self, exclude_id: Optional[int] = None) -> set:
        return {datetime.fromisoformat(p["due_at"]).astimezone(post_schedule.tz()).date()
                for p in self.db.list_scheduled_posts(("pending",)) if p["kind"] == "reel" and p["id"] != exclude_id}

    def post_due(self, now: Optional[datetime] = None) -> None:
        """Publishes every post whose slot has come. A Reel slot missed by more than
        MISSED_SLOT_GRACE (daemon off, or paused) moves to the next free slot instead of
        posting at an odd hour; Stories and carousels only follow a Reel that really posted.
        The daemon and the bot both call this; one pass runs at a time, the other skips."""
        db_path = getattr(self.db, "db_path", None)
        if db_path is None:
            return self._post_due(now)
        from src.publication_lock import exclusive
        with exclusive(db_path, SCHEDULED_POSTING_LOCK) as mine:
            if not mine:
                log.info("Another process is posting scheduled items; skipping this pass.")
                return None
            return self._post_due(now)

    def _post_due(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        # Each booking on its own (QF-110): a refusal or error on one never stops the others.
        #  - ReleaseBlocked (the release checks refused this version): "blocked", said once with what can work.
        #  - an upload whose outcome is unknown (the run is publish_unknown): the booking is "unknown", never posted
        #    again by this loop, and It posted / It didn't post decides (the existing reconciliation).
        #  - anything else (an upload in progress elsewhere, its lock held, a locked report, the network, a checker
        #    without a verdict): kept booked, tried again, said once per distinct error.
        from src.release import ReleaseBlocked
        for post in self.db.list_scheduled_posts(("pending",)):
            try:
                self._post_one_due(post, now)
            except ReleaseBlocked as e:
                self._block_booking(post, e, now)
            except Exception as e:  # one booking's failure is said, and the others still post
                if not isinstance(e, ValueError):
                    log.exception("Booking %s (%s) could not post.", post["id"], post["kind"])
                if post["kind"] == "reel" and (self.db.get_run(post["run_id"]) or {}).get("status") == "publish_unknown":
                    self._unknown_booking(post, e)
                else:
                    self._hold_booking(post, e)
        self._post_carousel_if_due(now)

    def _post_one_due(self, post: dict[str, Any], now: datetime) -> None:
        due = datetime.fromisoformat(post["due_at"])
        if due > now or not self._may_post(post["run_id"]):
            return
        run = self.db.get_run(post["run_id"]) or {}
        if post["kind"] == "reel":
            if now - due > MISSED_SLOT_GRACE:
                self._reschedule(post, run, now)
                return
            res = self.publish_run(post["run_id"])
            self.db.update_scheduled_post(post["id"], res["status"], detail={
                "error": res.get("publish_result", {}).get("error"), "posted_at": now.isoformat()})
            if res["status"] != "published":
                for other in self.db.list_scheduled_posts(("pending", "waiting"), run_id=post["run_id"]):
                    self.db.update_scheduled_post(other["id"], "cancelled", detail={"reason": "the Reel did not post"})
        elif post["kind"] == "story":
            if run.get("status") == "scheduled":
                return  # its Reel was moved; the Story moved with it
            if run.get("status") != "published":
                self.db.update_scheduled_post(post["id"], "cancelled", detail={"reason": "the Reel did not post"})
                return
            self._post_photos(post, run, "story")

    def _block_reasons(self, run_id: str, exc: Exception) -> list[str]:
        """Why the release refused, in plain words: from the release report it has just written, else its message."""
        from src.release import plain_block_reasons
        if str(exc).startswith("Release blocked"):
            try:
                master = Path((self.db.get_run(run_id) or {}).get("master_video_path") or "")
                report = json.loads((master.parent / "release_report.json").read_text(encoding="utf-8"))
                found = plain_block_reasons(report)
                if found:
                    return found
            except (OSError, ValueError, TypeError):
                pass
        return [str(exc).removeprefix("Release blocked: ")[:400]]

    def _still(self, post: dict[str, Any]) -> bool:
        """True while the booking is still as this pass read it (a Post now may have changed it meanwhile)."""
        return any(p["id"] == post["id"] for p in self.db.list_scheduled_posts((post["status"],), run_id=post["run_id"]))

    def _unknown_booking(self, post: dict[str, Any], exc: Exception) -> None:
        """The Reel's upload started and its outcome is unknown: the booking is "unknown" (this loop never posts it
        again), its Stories and carousel are cancelled as for any Reel that did not post for sure, and the operator is
        asked once whether it posted (the pubok/pubno reconciliation; a remake is never offered)."""
        if not self._still(post):
            return
        self.db.update_scheduled_post(post["id"], "unknown", detail={"error": _error_words(exc)})
        for other in self.db.list_scheduled_posts(("pending", "waiting"), run_id=post["run_id"]):
            self.db.update_scheduled_post(other["id"], "cancelled", detail={"reason": "the Reel did not post"})
        key = self.db.run_key(post["run_id"])
        text = (f"⚠️ <b>Publish unconfirmed</b>\n\n<b>{html.escape(self.db.run_label(post['run_id']))}</b>\n\n"
                f"The upload may have gone through ({html.escape(_error_words(exc))}). I won't retry automatically, "
                "to avoid a duplicate post. Check Facebook and Instagram, then tell me:")
        buttons = [{"text": "✅ It posted", "callback_data": f"pubok:{key}"},
                   {"text": "🔁 It didn't post", "callback_data": f"pubno:{key}"}]
        try:
            self._alert(text, buttons=buttons, group="needs")
        except TypeError:   # a stand-in alert that takes no group
            self._alert(text, buttons=buttons)

    def _block_booking(self, post: dict[str, Any], exc: Exception, now: datetime) -> None:
        """Sets a booking aside: it is not posted, and trying again cannot change that (QF-110). A Reel's day is
        freed for other episodes; its Stories and carousel wait with it. Said once, with what can work."""
        if not self._still(post):
            return
        reasons = self._block_reasons(post["run_id"], exc)
        label = html.escape(self.db.run_label(post["run_id"]))
        log.warning("Booking %s (%s, %s) blocked: %s", post["id"], post["kind"], post["run_id"], "; ".join(reasons))
        if post["kind"] != "reel":
            name = {"story": "Stories", "carousel": "Carousel"}.get(post["kind"], post["kind"])
            self.db.update_scheduled_post(post["id"], "failed", detail={"error": "; ".join(reasons)})
            _notice(self, f"❌ {name} not posted for {label}: " + html.escape("; ".join(reasons)[:300]), section="needs",
                         key=f"post-failed:{post['id']}", actions=self._episode_action(post["run_id"]))
            return
        self.db.update_scheduled_post(post["id"], "blocked", detail={
            "reason": "; ".join(reasons), "reasons": reasons, "blocked_at": now.isoformat(), "error": str(exc)[:1000]})
        from src.posting_view import BLOCKED_NEXT, blocked_buttons
        lines = "\n".join(f"• {html.escape(r)}" for r in reasons)
        text = f"⛔ <b>Not posted: {label}</b>\n\nIts Reel was held at its slot because:\n{lines}\n\n{BLOCKED_NEXT}"
        try:
            self._alert(text, buttons=blocked_buttons(self.db, post), group="needs")
        except TypeError:   # a stand-in alert that takes no group
            self._alert(text, buttons=blocked_buttons(self.db, post))

    def _hold_booking(self, post: dict[str, Any], exc: Exception) -> None:
        """Keeps a booking that hit an error which may pass: it is tried again at the next pass, and the error is
        said once (again only when it changes), never every minute (QF-110)."""
        from src.fact_evidence import RecheckUnavailable
        said = _error_words(exc)
        detail = dict(post.get("detail") or {})
        if detail.get("held") == said or not self._still(post):
            return
        self.db.update_scheduled_post(post["id"], post["status"], detail={**detail, "held": said})
        name = {"reel": "Reel", "story": "Stories", "carousel": "Carousel"}.get(post["kind"], post["kind"])
        why = (f"its fact check, made before a code update, must be run again under the current rules first, and "
               f"that cannot happen now ({html.escape(said)}). It is tried again with a growing wait"
               if isinstance(exc, RecheckUnavailable) else html.escape(said))
        _notice(self, f"⚠️ <b>{name} not posted yet</b> for {html.escape(self.db.run_label(post['run_id']))}: "
                    f"{why}\n\nIt stays booked and is tried again; you'll hear again only if the error changes.", section="needs", key=f"post-held:{post['id']}",
                     actions=self._episode_action(post["run_id"]))

    def _reschedule(self, post: Dict[str, Any], run: Dict[str, Any], now: datetime) -> None:
        slot = self._reel_slot(post["run_id"], now, self._booked_reel_days(exclude_id=post["id"]))
        self.db.update_scheduled_post(post["id"], "pending", due_at=slot.astimezone(timezone.utc).isoformat())
        for story in self.db.list_scheduled_posts(("pending",), run_id=post["run_id"]):
            if story["kind"] == "story":
                when = post_schedule.story_time(slot).astimezone(timezone.utc).isoformat()
                self.db.update_scheduled_post(story["id"], "pending", due_at=when)
        missed = post_schedule.fmt(datetime.fromisoformat(post["due_at"]))
        label = html.escape(self.db.run_label(post["run_id"]))
        detail = dict(post.get("detail") or {})
        held = detail.get("held")
        if held:   # held at its slot (QF-110): said with why, and once per reason, never again each day
            if detail.get("moved_said") == held:
                return
            self.db.update_scheduled_post(post["id"], "pending", detail={**detail, "moved_said": held})
            _notice(self, f"⏰ {label} could not post at its {html.escape(missed)} slot: it is still held "
                        f"({html.escape(held)}). Moved to {html.escape(post_schedule.fmt(slot))}; it keeps moving to "
                        "the next free slot until it can post, and you'll hear again only if the reason changes.", section="needs", key=f"missed:{post['id']}",
                         actions=self._episode_action(post["run_id"]))
            return
        _notice(self, f"⏰ Missed the {html.escape(missed)} slot for {label} "
                    f"(the daemon was off or paused). Moved to {html.escape(post_schedule.fmt(slot))}.", section="needs", key=f"missed:{post['id']}",
                     actions=self._episode_action(post["run_id"]))

    CAROUSEL_CAPTION_MAX = 2200  # Instagram's caption and comment limit

    @staticmethod
    def _carousel_caption(payload: Dict[str, Any], slides: Optional[List[str]] = None) -> str:
        """The carousel's caption (see _carousel_post)."""
        return PipelineCoordinator._carousel_post(payload, slides)[0]

    def _carousel_request(self, run_id: str, payload: dict[str, Any], slides: list[str]) -> tuple[str, str]:
        """(caption, first comment) the carousel posts: its caption as the run's review card showed it (the
        hashtags from the topic's list; publishable.carousel_post_base raises ValueError when the card showed
        another), then the photo credits (_carousel_post). QF-43."""
        from src import publishable
        _, final = publishable.carousel_post_base(payload, self.db, run_id,
                                                  (self.db.get_run(run_id) or {}).get("topic_id", ""))
        return self._carousel_post(payload, slides, hashtags=final)

    @staticmethod
    def _carousel_post(payload: Dict[str, Any], slides: Optional[List[str]] = None,
                       hashtags: list[str] | None = None) -> Tuple[str, str]:
        """(caption, first comment) for a carousel. The caption is the carousel's own text (Meta and Upload-Post
        flag duplicate captions; older scripts without one fall back to the Reel caption), with no hashtag outside
        `hashtags` when given (publishable.carousel_base: the topic's list, as the review card shows), then a credit line
        for every photo the slides show whose licence asks for one (credits.asset_attribution, read from each
        slide's record): the Reel caption's first credit line covered neither every photo nor only these
        (platform audit, 2026-10-28). Credits that would take the caption over Instagram's 2,200 characters go
        in the first comment instead. A slide without a record (drawn before records were kept) adds the Reel
        caption's summary credit line, as before."""
        from src.multi_format import carousel_credits
        if hashtags is not None:
            from src.publishable import carousel_base
            base = carousel_base(payload, hashtags)
        else:
            base = (payload.get("carousel_caption") or "").strip() or payload.get("caption", "")
        slides = [Path(s) for s in slides or []]
        if slides and (slides[0].parent.parent / "carousel_fb").is_dir():  # one caption goes to both platforms:
            # credit what Facebook's copy shows as well (a carousel packaged before rc68 matched its photos apart)
            slides += sorted((slides[0].parent.parent / "carousel_fb").glob("carousel_slide_*.jpg"))
        lines, readable = carousel_credits(slides)
        if not readable:  # some slide has no record: the Reel caption's summary line covers what it showed
            from src.credits import CREDIT_ADAPTED
            from src.multi_format import CAROUSEL_ADAPTED
            borrowed = [ln.replace(CREDIT_ADAPTED, "") for ln in payload.get("caption", "").splitlines()
                        if ln.startswith(beat_images.CREDIT_PREFIXES)][:1]
            lines = [ln for ln in lines if ln != CAROUSEL_ADAPTED] + borrowed + ([CAROUSEL_ADAPTED] if borrowed or lines else [])
        have = {ln.strip() for ln in base.splitlines()}
        missing = [ln for ln in lines if ln.strip() not in have]
        if not missing:
            return base, ""
        limit = PipelineCoordinator.CAROUSEL_CAPTION_MAX
        block = "\n".join(missing)
        if len(base.rstrip()) + 2 + len(block) <= limit:
            return base.rstrip() + "\n\n" + block, ""
        comment, left = [], []
        for ln in missing:
            (comment if len("\n".join(comment + [ln])) <= limit else left).append(ln)
        if left:
            log.warning("Carousel credits do not fit the caption or its first comment; %d line(s) left out: %s",
                        len(left), "; ".join(left)[:300])
        return base, "\n".join(comment)

    def _post_photos(self, post: Dict[str, Any], run: Dict[str, Any], kind: str) -> str:
        files = [f for f in post["files"] if Path(f).exists()]
        if not files:
            self.db.update_scheduled_post(post["id"], "failed", detail={"error": "image files are missing"})
            return "failed"
        if kind == "story":   # frames redrawn after booking must still carry their photo credits (2026-10-29)
            from src.release import story_credit_problems
            problems = story_credit_problems(Path(files[0]).parent.parent) or []
            if problems:
                self.db.update_scheduled_post(post["id"], "failed", detail={"error": "Story credits: " + "; ".join(problems)})
                _notice(self, f"❌ Stories not posted for {html.escape(self.db.run_label(post['run_id']))}: "
                            + html.escape("; ".join(problems)[:300]), section="needs", key=f"post-failed:{post['id']}",
                             actions=[{"text": "Retry", "callback_data": f"retrypost:{post['id']}"}])
                return "failed"
        try:
            caption, comment = (self._carousel_request(post["run_id"], run["script_payload"], files) if kind == "carousel"
                                else ("", ""))
        except ValueError as e:   # its caption is not the one the review card showed (QF-43)
            self.db.update_scheduled_post(post["id"], "failed", detail={"error": str(e)})
            _notice(self, f"❌ Carousel not posted for {html.escape(self.db.run_label(post['run_id']))}: "
                        + html.escape(str(e)[:300]), section="needs", key=f"post-failed:{post['id']}",
                         actions=[{"text": "Retry", "callback_data": f"retrypost:{post['id']}"}])
            return "failed"
        res = self.publisher.publish_photos(post["run_id"], run["topic_id"], kind, files, caption=caption,
                                            **({"first_comment": comment} if comment else {}))
        self.db.update_scheduled_post(post["id"], res["status"], detail=res)
        name = {"story": "Stories", "carousel": "Carousel"}[kind]
        label = html.escape(self.db.run_label(post["run_id"]))
        held = "; ".join((res.get("held_platforms") or {}).values())   # QF-65a: AI media never posted unlabelled
        if res["status"] == "published":
            _notice(self, f"✅ {name} posted for {label}" + (f" (with the AI label; {html.escape(held[:300])})" if held else ""), section="info")
        elif res["status"] == "unknown":
            _notice(self, f"⚠️ <b>Not sure the {name.lower()} posted</b> for {label} "
                        f"({html.escape(str(res.get('error', ''))[:200])}). Not retried, to avoid a duplicate; please check.", section="needs",
                         key=f"post-failed:{post['id']}", actions=self._episode_action(post["run_id"]))
        else:
            _notice(self, f"❌ {name} not posted for {label}: {html.escape(str(res.get('error', ''))[:300])}", section="needs",
                         key=f"post-failed:{post['id']}", actions=[{"text": "Retry", "callback_data": f"retrypost:{post['id']}"}])
        return res["status"]

    def _spacing_holds(self, post: dict[str, Any], run: dict[str, Any], slot: datetime) -> bool:
        """QF-73: whether sensitive-topic spacing keeps this ready carousel from its slot. Its booking records why
        (detail "waiting_for": "spacing", "spacing": the reason) and keeps it: a carousel spacing held back that then
        loses its spaced slot to another carousel still waited for spacing (posting_view.carousel_outlook). Post
        anyway (post_carousel_anyway) sets spacing aside for this carousel."""
        detail = dict(post.get("detail") or {})
        if detail.get("spacing_override"):
            return False
        problem = self.db.topic_problem(run["topic_id"], slot, run_id=post["run_id"], spacing_only=True)
        if problem and (detail.get("waiting_for"), detail.get("spacing")) != ("spacing", problem):
            detail.update(waiting_for="spacing", spacing=problem, spacing_at=slot.isoformat())
            self.db.update_scheduled_post(post["id"], "waiting", detail=detail)
        return bool(problem)

    def _spacing_expiry(self, post: dict[str, Any], now: datetime) -> dict[str, Any] | None:
        """The detail an expiring carousel records when the carousel plan kept it from a slot for sensitive-topic
        spacing, or for a carousel you chose to post anyway (QF-73), else None (its detail is kept, as before)."""
        detail = dict(post.get("detail") or {})
        if detail.get("waiting_for") != "spacing" and not isinstance(detail.get("spacing_why"), dict):
            return None
        from src.posting_view import SPACING
        return {**detail, "reason": SPACING, "expired_at": now.isoformat()}

    def _watch_carousel_spacing(self, now: datetime) -> None:
        """QF-73: a carousel the carousel plan leaves no slot for, because sensitive-topic spacing held it back (also
        when a spaced slot is then lost to another carousel), because another carousel you chose to post anyway takes
        its slot, or because you chose to post it anyway and another choice holds the only slot, never expires
        silently. "No slot left" is decided once, by the plan itself (posting_view.carousel_outlook: the same order
        the slot loop uses), the answer every screen shows. At every pass of the posting loop, from the moment its
        Reel has posted (its window is then known), such a carousel raises ONE Needs-you alert, as soon as that is
        so: why, until when, Post anyway (only when the plan would then give it a slot: that slot is named) and Let it
        expire (what happens if you do nothing). Its booking keeps the reason, and once its deadline has passed it
        expires recording it. Spacing that waits only for an unconfirmed publication raises no alert: it is decided
        once that publication is known."""
        from src.posting_view import (
            SPACING,
            carousel_outlook,
            post_anyway_outcome,
            spacing_buttons,
            why_words,
        )
        outlook = None
        for post in self.db.list_scheduled_posts(("waiting",)):
            if post["kind"] != "carousel" or not self._may_post(post["run_id"]):
                continue
            outlook = outlook or carousel_outlook(self.db, now)
            wait = outlook["waits"].get(post["id"])
            if not wait or wait["earliest"] or wait["state"] != "posted":
                continue
            detail = dict(post.get("detail") or {})
            record = {"spacing_why": wait["why"], "deadline": wait["deadline"].isoformat()}
            if wait["kind"] == "spacing":
                record.update(waiting_for="spacing", spacing=wait["reason"] or detail.get("spacing") or "")
            if now > wait["deadline"]:
                self.db.update_scheduled_post(post["id"], "expired", detail={
                    **detail, **record, "reason": SPACING, "expired_at": now.isoformat()})
                log.info("Carousel %s of %s expired: %s.", post["id"], post["run_id"], why_words(wait["why"], past=True))
                continue
            if not wait["unconfirmed"] and any(detail.get(k) != v for k, v in record.items()):
                detail.update(record)
                self.db.update_scheduled_post(post["id"], "waiting", detail=detail)
            flag = "spacing_alert" if wait["kind"] == "spacing" else "slot_alert"
            if wait["unconfirmed"] or detail.get(flag):
                continue
            outcome = ({"slot": None, "blocked_by": None} if wait["override"]
                       else post_anyway_outcome(self.db, post, outlook=outlook))
            detail[flag] = now.isoformat()
            self.db.update_scheduled_post(post["id"], "waiting", detail=detail)
            label = html.escape(self.db.run_label(post["run_id"]))
            deadline = html.escape(post_schedule.fmt(wait["deadline"]))
            if wait["kind"] == "spacing" and not wait["lost"]:
                why = str(wait["reason"] or "it is too close to another sensitive topic's post")
                head = (f"⏳ <b>Carousel waiting: {SPACING}</b>\n\n<b>{label}</b>\n\nIts carousel would post too close "
                        f"to another sensitive topic ({html.escape(why[:1].lower() + why[1:])}), and no spaced "
                        f"carousel slot comes before its deadline, {deadline}.\n\n")
            else:
                title = f"Carousel waiting: {SPACING}" if wait["kind"] == "spacing" else "Carousel without a slot"
                head = (f"⏳ <b>{title}</b>\n\n<b>{label}</b>\n\nIts carousel has no slot before its deadline, "
                        f"{deadline}: {html.escape(why_words(wait['why']))}.\n\n")
            if outcome["slot"]:
                head += (f"▶️ Post anyway: it posts at {html.escape(post_schedule.fmt(outcome['slot']))}, after the "
                         "checks every carousel passes.\n")
            elif outcome["blocked_by"]:
                other = next((p for p in self.db.list_scheduled_posts(("waiting",)) if p["id"] == outcome["blocked_by"]),
                             None)
                head += ("Post anyway can't give it a slot: the carousel of "
                         f"{html.escape(self.db.run_label(other['run_id']) if other else 'another episode')}, which you "
                         "chose to post anyway, holds it.\n")
            text = head + "⏹ Let it expire: nothing is posted. If you do nothing, it expires at its deadline."
            # The clean chat (UI-01): one Needs-you item per carousel booking, under the key its holds use, so a later
            # notice for it (no slot, then held at its slot) replaces this one; Post anyway and Let it expire are its
            # actions and close it only when they were done (notifications.Outcome). With the switch off, say()
            # leaves the item fields out and the notice is sent exactly as before.
            _notice(self, text, buttons=spacing_buttons(post, {"next": outcome["slot"]}), group="needs",
                    section="needs", key=f"post-failed:{post['id']}", run=post["run_id"], replace=True)

    def _spaced_carousel(self, post_id: Any) -> dict[str, Any] | None:
        """The waiting carousel a QF-73 alert's button names, while the plan still keeps it from a slot, or it waited
        for spacing, or you chose Post anyway for it."""
        from src.posting_view import spacing_wait
        post = next((p for p in self.db.list_scheduled_posts(("waiting",)) if str(p["id"]) == str(post_id)), None)
        if post is None or post["kind"] != "carousel":
            return None
        detail = post.get("detail") or {}
        return post if (spacing_wait(self.db, post) or detail.get("waiting_for") == "spacing"
                        or detail.get("spacing_override") or isinstance(detail.get("spacing_why"), dict)) else None

    def post_carousel_anyway(self, post_id: Any) -> str:
        """The spacing alert's ▶️ Post anyway (QF-73): spacing is set aside for this one carousel, which goes first
        at the slot the carousel plan then gives it (posting_view.post_anyway_outcome), and the reply names that
        slot; the checks every carousel passes there (QF-81 draw and audit, the approved review, its slides) still
        apply. When a carousel you chose to post anyway earlier holds the only slot, nothing is changed and the reply
        says so (the first choice keeps its slot).

        The reply is a notifications.Outcome (UI-01): done only when spacing is set aside and the plan gives it a
        slot, so its item closes then and stays open, saying why, on every refusal."""
        from src.notifications import Outcome
        from src.posting_view import carousel_outlook, post_anyway_outcome
        post = self._spaced_carousel(post_id)
        if post is None:
            return Outcome("🖼 That carousel is not waiting for sensitive-topic spacing any more: see Posting.",
                           done=False)
        label = html.escape(self.db.run_label(post["run_id"]))
        now = datetime.now(UTC)
        outlook = carousel_outlook(self.db, now)
        outcome = post_anyway_outcome(self.db, post, outlook=outlook)
        reel_at = post_schedule.carousel_reel(self.db, post)[1]
        deadline = html.escape(post_schedule.fmt(post_schedule.carousel_deadline(reel_at)))
        waiting = {p["id"]: p for p in self.db.list_scheduled_posts(("waiting",))}
        if outcome["blocked_by"]:
            other = waiting.get(outcome["blocked_by"])
            held = outlook["plan"].get(outcome["blocked_by"])
            return Outcome(f"🖼 The carousel of {label} can't take that slot: the carousel of "
                           f"{html.escape(self.db.run_label(other['run_id']) if other else 'another episode')}, which "
                           "you chose to post anyway first, holds its only carousel slot"
                           + (f", {html.escape(post_schedule.fmt(held))}" if held else "")
                           + f". Nothing was changed. If you do nothing, it expires at its deadline, {deadline}.",
                           done=False)
        slot = outcome["slot"]
        if not slot:
            return Outcome(f"🖼 The carousel of {label} has no carousel slot left before its deadline, so it can't "
                           "post.", done=False)
        if not (post.get("detail") or {}).get("spacing_override"):
            self.db.update_scheduled_post(post["id"], "waiting", detail={
                **(post.get("detail") or {}), "spacing_override": True, "spacing_override_at": now.isoformat(),
                "priority": True})
        first = post_schedule.first_carousel_slot(reel_at, now=now - MISSED_SLOT_GRACE)
        where = "its next carousel slot, " if slot == first else ""
        gone = [html.escape(self.db.run_label(waiting[pid]["run_id"])) for pid in outcome["displaced"] if pid in waiting]
        return Outcome(f"▶️ The carousel of {label} posts at {where}{html.escape(post_schedule.fmt(slot))}, if its "
                       "checks pass. Sensitive-topic spacing is set aside for this carousel only."
                       + (f" The carousel of {', '.join(gone)} gives up that slot and has none left before its "
                          "deadline: you will get a notice for it." if gone else ""), done=True)

    def expire_spaced_carousel(self, post_id: Any) -> str:
        """The alert's ⏹ Let it expire (QF-73): it expires now, its booking recording why. The reply is a
        notifications.Outcome (UI-01): done only when it expired."""
        from src.notifications import Outcome
        from src.posting_view import SPACING, spacing_wait
        post = self._spaced_carousel(post_id)
        if post is None:
            return Outcome("🖼 That carousel is not waiting for sensitive-topic spacing any more: see Posting.",
                           done=False)
        wait = spacing_wait(self.db, post) or {}
        self.db.update_scheduled_post(post["id"], "expired", detail={
            **(post.get("detail") or {}), **({"spacing_why": wait["why"]} if wait.get("why") else {}),
            "reason": SPACING, "by": "operator", "expired_at": datetime.now(UTC).isoformat()})
        return Outcome(f"⏹ The carousel of {html.escape(self.db.run_label(post['run_id']))} won't post: you let it "
                       f"expire ({SPACING}). Its Reel and other posts are not affected.", done=True)

    def _post_carousel_if_due(self, now: datetime) -> None:
        self._watch_carousel_spacing(now)   # QF-73: a carousel spacing leaves no slot for is said, never silent
        local = now.astimezone(post_schedule.tz())
        slot = post_schedule.carousel_time(local.date())
        if not slot or not slot <= now < slot + MISSED_SLOT_GRACE:
            return
        if self.db.get_setting("carousel_posted_on") == local.date().isoformat():
            return
        waiting = [p for p in self.db.list_scheduled_posts(("waiting",)) if self._may_post(p["run_id"])]
        ready = []
        # One rule with Posting and the booking's confirmation (post_schedule.carousel_may_take), measured at the
        # slot: from 48 h after its Reel posted (or its slot, while it hasn't) to its deadline, 7 days after it or its
        # first carousel slot when later
        for post in waiting:
            state, reel_at = post_schedule.carousel_reel(self.db, post)
            if post_schedule.carousel_expired(slot, reel_at):  # a week-old carousel is stale next to newer Reels
                self.db.update_scheduled_post(post["id"], "expired", detail=self._spacing_expiry(post, now))
            elif state == "posted" and post_schedule.carousel_may_take(slot, reel_at):
                run = self.db.get_run(post["run_id"])
                # QF-73: a sensitive topic's carousel too close to another sensitive topic's post waits for a later
                # slot (it stays waiting, its reason recorded; another ready episode may take this one), unless you
                # chose Post anyway for it
                if run and run["status"] == "published" and not self._spacing_holds(post, run, slot):
                    ready.append((post, run))
        from src import carousel_lazy
        # the newest topic; a carousel the operator asked to redraw after a hold goes first (QF-81). One that no
        # longer matches the review you approved (QF-81b) can never post: it is set aside before the slot is taken,
        # so the next ready episode takes the slot in its place. So can one with nothing left to post (QF-81c): its
        # slides gone (any of them: QF-81d), or (booked undrawn) its plan missing, unreadable, corrupt or without a
        # photo to draw; it is held with one alert.
        for post, run in sorted(ready, key=lambda pr: (bool((pr[0].get("detail") or {}).get("priority")), pr[0]["id"]),
                                reverse=True):
            run_dir = Path(run["master_video_path"]).parent
            if post["files"]:
                # a drawn booking posts exactly the deck it booked, or nothing (QF-81d): never what is left of it
                gone = [f for f in post["files"] if not Path(f).is_file()]
                if gone:
                    where = Path(post["files"][0]).parent.name
                    self._hold_carousel(post, carousel_lazy.CarouselHeld(
                        f"its {len(post['files'])} slides in {where}/ are no longer on disk"
                        if len(gone) == len(post["files"]) else
                        f"{len(gone)} of its {len(post['files'])} slides in {where}/ are no longer on disk "
                        f"({', '.join(Path(f).name for f in gone[:3])}{', …' if len(gone) > 3 else ''}), so the rest "
                        f"is not the carousel you approved"), final=True)
                    continue
                broken = self._approval_broken(post["run_id"])   # its slides' bytes, bound by the review card
                if not broken:
                    break
                self._hold_carousel(post, carousel_lazy.CarouselHeld(broken), binding=True)
                continue
            missing = carousel_lazy.plan_problem(run_dir)
            if missing:
                self._hold_carousel(post, carousel_lazy.CarouselHeld(missing), final=True)
                continue
            broken = self._approval_broken(post["run_id"])
            if not broken:
                break
            self._hold_carousel(post, carousel_lazy.CarouselHeld(broken), binding=True)
        else:
            return
        taken_on = self.db.get_setting("carousel_posted_on")
        self.db.set_setting("carousel_posted_on", local.date().isoformat())  # before posting: never twice a day
        if not post["files"]:
            # Only the episode that takes the slot is drawn (QF-81), or its preview's drawing reused when nothing
            # changed; the slot is taken first, so a held carousel is never drawn again the same day, nor again by
            # itself later: the operator's "Redraw" button books it for the next slot.
            missing = broken = None
            try:
                from src.publication_lock import publication_lock
                with publication_lock(self.db.db_path, "carousel:" + post["run_id"], wait=900):
                    # checked again under the lock (QF-81c): a plan changed or removed while the lock was awaited is
                    # never drawn
                    missing = carousel_lazy.plan_problem(run_dir)
                    broken = None if missing else self._approval_broken(post["run_id"])
                    if not (missing or broken):
                        files = carousel_lazy.render(run_dir, run["script_payload"], self.multiformat_gen,
                                                     QualityAuditor())
            except (carousel_lazy.CarouselHeld, ValueError) as e:
                self._hold_carousel(post, e)
                return
            except OSError as e:   # a file that could not be read, written or installed: held, with the reason
                self._hold_carousel(post, carousel_lazy.CarouselHeld(f"its slides could not be drawn or installed ({e})"))
                return
            if missing or broken:   # nothing was drawn or posted: the slot is left to the next ready episode
                self._hold_carousel(post, carousel_lazy.CarouselHeld(missing or broken), binding=bool(broken),
                                    final=bool(missing))
                self.db.set_setting("carousel_posted_on", taken_on or "")
                return
            self.db.update_scheduled_post(post["id"], "waiting", files=files)
            post = {**post, "files": files}
        self._post_photos(post, run, "carousel")

    def _approved_fingerprint(self, run_id: str) -> str | None:
        """The run's review fingerprint when it is still the one on the review card you approved (QF-110: the
        copy, video, cover, Stories and carousel unchanged since), else None."""
        from src.telegram_state import get, review_fingerprint
        approval = self.db.get_approval(run_id) or {}
        if approval.get("decision") != "approved" or approval.get("telegram_message_id") is None:
            return None
        try:
            shown = get(self.db, "review-card", f"{run_id}:{approval['telegram_message_id']}")
            now = review_fingerprint(self.db, run_id)
        except Exception:  # noqa: BLE001 - no card record (a command-line database) or a file missing: no binding
            return None
        return now if shown and shown == now else None

    def _approval_broken(self, run_id: str) -> str | None:
        """Why the run no longer matches the review card you approved (QF-81b), or None. Only a Telegram approval
        whose card fingerprint was recorded binds (as _approved_fingerprint): an approval with no card record (the
        command line, the auto-approve flag) is not bound, as before."""
        from src.telegram_state import get, review_fingerprint
        approval = self.db.get_approval(run_id) or {}
        if approval.get("decision") != "approved" or approval.get("telegram_message_id") is None:
            return None
        shown = get(self.db, "review-card", f"{run_id}:{approval['telegram_message_id']}")
        if not shown:
            return None
        try:
            now = review_fingerprint(self.db, run_id)
        except Exception as e:  # noqa: BLE001 - a reviewed file missing or unreadable: held, with the reason
            return f"a file of the review you approved can no longer be read ({e})"
        if now != shown:
            return "its plan or files changed after you approved the review, so it is not the carousel you approved"
        return None

    RECHECK_WAIT_PREFIX = "fact_recheck_wait:"
    RECHECK_MAX_TRIES = 6            # then it waits for Post now (which tries once more)
    RECHECK_FIRST_WAIT_S = 300       # doubling: 5, 10, 20, 40, 80 min

    def _recheck_facts(self, run: dict[str, Any], retry_now: bool = False) -> None:
        """QF-110: a fact review made before a code update changed the checkers' rules or the copy's fingerprint is
        checked again under the current rules, on the copy you approved (never another), once per copy and rules;
        the signed result, pass or fail, is kept beside the master (fact_evidence.RECHECK_FILE) for the release gate.
        A changed sheet, reference file or copy is not re-checked: the gate blocks it. A checker that gives no verdict
        (quota, rate limit, outage) raises RecheckUnavailable and nothing is stored: the booking is held and tried
        again after a doubling wait, at most RECHECK_MAX_TRIES times (Post now tries once more). While a run is being
        made the checkers are left to it (the free tier stays serial): the re-check waits and the booking is held."""
        from src import fact_evidence as fe
        payload = run.get("script_payload") or {}
        payload = {**payload, "topic_id": payload.get("topic_id") or run.get("topic_id")}   # a documentary's has none
        if not run.get("master_video_path") or not fe.fact_review_problem(payload) or not fe.drift_only(payload):
            return
        approved = self._approved_fingerprint(run["run_id"])
        if not approved:
            return
        path = Path(run["master_video_path"]).parent / fe.RECHECK_FILE
        try:
            if fe.recheck_matches(payload, json.loads(path.read_text(encoding="utf-8")), approved):
                return
        except (OSError, ValueError):
            pass
        key = self.RECHECK_WAIT_PREFIX + run["run_id"]
        try:
            state = json.loads(self.db.get_setting(key) or "{}")
        except ValueError:
            state = {}
        if self.db.get_active_progress() is not None:
            raise fe.RecheckUnavailable("a run is using the checkers now; checked when it has finished")
        if not retry_now:
            if int(state.get("tries") or 0) >= self.RECHECK_MAX_TRIES:
                raise fe.RecheckUnavailable(f"the checkers gave no verdict {state['tries']} times; tap Post now to try "
                                            "once more")
            if float(state.get("next_at") or 0) > time.time():
                raise fe.RecheckUnavailable(str(state.get("why") or "waiting before the next try"))
        from src import trace_check
        models = trace_check.checker_models()
        try:
            rec = fe.run_recheck(payload, approved)
        except fe.RecheckUnavailable as e:
            tries = int(state.get("tries") or 0) + 1
            self.db.set_setting(key, json.dumps({"tries": tries, "why": str(e)[:200],
                                                 "next_at": time.time() + self.RECHECK_FIRST_WAIT_S * 2 ** (tries - 1)}))
            raise
        self.db.set_setting(key, "")
        from src.atomic_files import write_json
        write_json(path, rec)
        log.info("Fact check of %s made before a code update, checked again under the current rules: %s.",
                 run["run_id"], "passed" if rec.get("passed") else "did not pass")
        paid = not all("gemini" in str(m).lower() for m in models.values())
        _notice(self, f"🔎 <b>Fact check run again</b> for {html.escape(self.db.run_label(run['run_id']))} before it "
                    "posts (a code update since you approved it): trace check "
                    f"({html.escape(str(models.get('trace_check')))}) and evidence check "
                    f"({html.escape(str(models.get('evidence_check')))}), "
                    + ("paid API calls" if paid else "free tier") + ". "
                    + ("Passed." if rec.get("passed") else "It did not pass, so the Reel is held."), section="info")

    def _require_release(self, run: dict[str, Any], dry_run: bool = False, retry_now: bool = False) -> dict[str, Any]:
        """The release gate, after the fact re-check a code update calls for (QF-110)."""
        from src.release import require_release
        if not dry_run:
            self._recheck_facts(run, retry_now=retry_now)
        return require_release(run, dry_run=dry_run)

    def _hold_carousel(self, post: dict[str, Any], held: Exception, binding: bool = False,
                       final: bool = False) -> None:
        """A carousel its slot could not post (carousel_lazy): its booking says why, and one alert gives every
        finding of its drawing and the button that redraws it and posts it in the next slot.

        binding: it no longer matches the review you approved (QF-81b). Its Reel is posted, so that review cannot be
        approved again and a redraw would only hold it again: the alert says it will not post and offers only its
        Details (and the 🏠 every notice has).

        final: nothing is left to draw or post from (QF-81c: its slides gone, all or some (QF-81d), or its plan missing,
        unreadable, corrupt or without a photo to draw), so a redraw cannot help either: the same alert, naming what
        is missing."""
        from src import carousel_lazy
        if binding or final:
            self.db.update_scheduled_post(post["id"], "failed", detail={
                "error": f"held at its slot: {held}", "held": True, ("binding" if binding else "final"): True})
            _notice(self, f"❌ <b>Carousel not posted</b> for {html.escape(self.db.run_label(post['run_id']))}: "
                    f"{html.escape(str(held)[:400])}." + chr(10) * 2
                    + "This carousel will not post. Nothing was posted, and its Reel and other posts are not "
                      "affected.",
                    buttons=[[{"text": "📋 Details", "callback_data": f"cpost:{self.db.run_key(post['run_id'])}"}]],
                    section="needs", key=f"post-failed:{post['id']}", run=post["run_id"], replace=True)
            return
        record = getattr(held, "record", None) or {}
        self.db.update_scheduled_post(post["id"], "failed", detail={"error": f"held at its slot: {held}", "held": True})
        found = carousel_lazy.summary(record, limit=2400)
        # The clean chat (UI-01): one Needs-you item per held booking (its key), its Redraw an item action that
        # closes it only when redraw_carousel says it was done (notifications.Outcome); the earlier chat's message
        # is sent exactly as before (say() leaves the item fields out with the switch off).
        _notice(self, f"❌ <b>Carousel held</b> for {html.escape(self.db.run_label(post['run_id']))}: "
                f"{html.escape(str(held)[:400])}" + (chr(10) * 2 + html.escape(found) if found else "")
                + chr(10) * 2 + "Nothing was posted. Redraw it once the cause is dealt with; it then posts in the "
                  "next carousel slot, ahead of newer episodes.",
                buttons=[[{"text": "🔁 Redraw and post next slot", "callback_data": f"carredraw:{post['id']}"}]],
                section="needs", key=f"post-failed:{post['id']}", run=post["run_id"], replace=True)

    def redraw_carousel(self, post_id: Any) -> str:
        """The hold alert's 🔁: the held carousel's drawing is discarded and it waits again, first in line for the
        next carousel slot inside its window, where it is drawn afresh and audited before it posts (QF-81).

        The reply is a notifications.Outcome (UI-01): done only when it waits again for a slot, so the hold's item
        closes then and stays open, saying why, on every refusal."""
        from src import carousel_lazy
        from src.notifications import Outcome
        post = next((p for p in self.db.list_scheduled_posts(("failed",)) if str(p["id"]) == str(post_id)), None)
        if post is None or post["kind"] != "carousel" or not (post.get("detail") or {}).get("held"):
            return Outcome("🖼 That carousel is not held any more: see Posting.", done=False)
        run = self.db.get_run(post["run_id"]) or {}
        if not run.get("master_video_path"):
            return Outcome("🖼 That episode has no run folder to draw from.", done=False)
        if post["files"] and post["detail"].get("final"):   # QF-81d: a redraw makes a new deck, not the one booked
            return Outcome(f"🖼 The carousel of {html.escape(self.db.run_label(post['run_id']))} can't be redrawn: "
                           "its booked slides are gone, and a new drawing would not be the carousel you approved. It "
                           "will not post, and nothing was changed.", done=False)
        broken = self._approval_broken(post["run_id"])
        if broken:   # QF-81b: it would only be held again at its slot, and take that slot from a newer episode
            return Outcome(f"🖼 The carousel of {html.escape(self.db.run_label(post['run_id']))} can't be redrawn: "
                           f"{html.escape(broken)}. It will not post, and nothing was changed.", done=False)
        missing = carousel_lazy.plan_problem(Path(run["master_video_path"]).parent)
        if missing:  # QF-81c: a redraw draws from this plan, so it would only be held again
            return Outcome(f"🖼 The carousel of {html.escape(self.db.run_label(post['run_id']))} can't be redrawn: "
                           f"{html.escape(missing)}. It will not post, and nothing was changed.", done=False)
        carousel_lazy.discard(Path(run["master_video_path"]).parent)
        self.db.update_scheduled_post(post["id"], "waiting", files=[],
                                      detail={"redraw": True, "priority": True, "held_before": post["detail"].get("error")})
        car = post_schedule.first_carousel_slot(post_schedule.carousel_reel(self.db, post)[1],
                                                now=datetime.now().astimezone())
        label = html.escape(self.db.run_label(post["run_id"]))
        if not car:
            self.db.update_scheduled_post(post["id"], "expired")
            return Outcome(f"🖼 The carousel of {label} has no carousel slot left in its window, so it won't post.",
                           done=False)
        return Outcome(f"🔁 The carousel of {label} is drawn again and posts {html.escape(post_schedule.fmt(car))}, if "
                       "its checks pass.", done=True)

    def preview_carousel(self, run_id: str) -> dict[str, Any]:
        """The review card's 🖼 Preview carousel: both copies drawn and audited as the slot would (carousel_lazy.draw;
        reused while nothing changed, so the slot reuses it too). Returns the drawing's record with "run_dir"."""
        from src import carousel_lazy
        from src.publication_lock import publication_lock
        run = self.db.get_run(run_id) or {}
        if not run.get("master_video_path"):
            raise ValueError("This run has no run folder.")
        run_dir = Path(run["master_video_path"]).parent
        if not carousel_lazy.planned(run_dir):
            raise ValueError("This run has no carousel to preview.")
        with publication_lock(self.db.db_path, "carousel:" + run_id, wait=900):
            record = carousel_lazy.draw(run_dir, run.get("script_payload") or {}, self.multiformat_gen, QualityAuditor())
        return {**record, "run_dir": str(run_dir)}

    def publish_run(self, run_id: str, dry_run: bool = False, previous_status: str = "queued") -> Dict[str, Any]:
        from src.publication_lock import publication_lock
        with publication_lock(self.db.db_path, run_id, wait=30):
            return self._publish_run_locked(run_id, dry_run, previous_status)

    def _timed_upload(self, run: dict[str, Any], upload):
        """An upload, timed as the run's "upload" stage; its stage budgets are checked once it ends (QF-74)."""
        from src.stage_timer import file_findings
        run_dir = Path(run["master_video_path"]).parent
        try:
            with StageTimer(run_dir).stage("upload"):
                return upload()
        finally:
            file_findings(self.db, run["run_id"], run_dir)

    def _publish_run_locked(self, run_id: str, dry_run: bool = False, previous_status: str = "queued") -> Dict[str, Any]:
        self._check_cancel(run_id)
        run = self.db.get_run(run_id)
        if not run or not run.get("master_video_path"):
            raise ValueError(f"Run '{run_id}' has no rendered video.")
        if run.get("status") == "published":
            return {"status": "published", "already_published": True}
        if run.get("status") in ("publishing", "publish_unknown"):
            raise ValueError("Publication is in progress or its outcome is unknown; reconcile it before retrying.")
        payload, tid = run["script_payload"], run["topic_id"]
        payload = {**payload, "topic_id": payload.get("topic_id") or tid}  # a documentary's payload had none
        from src.delivery_profiles import get_delivery_profile
        profile_name = payload.get("delivery_profile") or ("deep_dive" if run.get("content_format") == "deep_dive" else "reel")
        profile = get_delivery_profile(profile_name).name
        if profile not in ("reel_9x16", "deep_dive_16x9"):
            raise ValueError("No uploader is configured for this delivery profile; use the exported artifacts.")
        if not dry_run and payload.get("origin") == "curated" and not payload.get("human_reviewed"):
            from src.release import ReleaseBlocked
            raise ReleaseBlocked("This run used an unreviewed curated script; it can only be published after review.")
        self._require_release(run, dry_run=dry_run)
        envelope = self.publisher.build_envelope(
            run_id, Path(run["master_video_path"]), Path(run["thumbnail_cover_path"]),
            Path(run["thumbnail_grid_path"]), payload,
            platforms=["youtube"] if profile == "deep_dive_16x9" else None,
        )
        if dry_run:
            self.db.update_run(run_id, "approved")
        elif not self.db.claim_publication(run_id):
            # QF-73: a sensitive topic too close to another one is refused with that reason
            raise ValueError(self.db.topic_problem(run["topic_id"], run_id=run_id, spacing_only=True)
                             or "Publication was already claimed or this run is not ready")
        try:
            pub = self._timed_upload(run, lambda: self.publisher.publish_reel(envelope, dry_run=dry_run))
        except Exception as exc:
            if not dry_run:
                self.db.update_run(run_id, "publish_unknown", error=type(exc).__name__)
                self.topic_engine.record_status(tid, "publish_unknown", run_id=run_id)
                self.db.record_publication(tid, run_id, "coordinator", "unknown",
                                           {"error_type": type(exc).__name__})
            raise
        status = pub["status"]
        receipt = None
        shorts = config.YOUTUBE_SHORTS and profile == "reel_9x16"
        if (status == "published" and config.TELEGRAM_CARDS_V2 and hasattr(self, "telegram_bot")
                and hasattr(self.telegram_bot, "create_publish_receipt")):
            # Made once the Reel's outcome is known, from what was attempted: each platform live or failed as it
            # came out, the Short listed only when it will be attempted (it showed every platform "live", and
            # "Reel & Short" with Shorts off or failed: UI-02). A Reel that failed outright has the alert below.
            receipt = self.telegram_bot.create_publish_receipt(
                run_id, payload.get("title", tid), platforms=_receipt_platforms(envelope["platforms"], shorts),
                initial=_receipt_outcomes(pub, envelope["platforms"]))

        if status == "published":
            self.db.update_run(run_id, "published")
            self.topic_engine.record_status(tid, "published", run_id=run_id)
            log.info("Published %s.", run_id)
            if not receipt:
                urls, missed = pub.get("post_urls") or {}, _missing_reasons(pub)
                links = "\n".join(f"✅ {html.escape(_PLATFORM_NAMES.get(p, p))}: {html.escape(str(urls.get(p) or 'posted'))}"
                                  for p in envelope["platforms"] if p not in missed)
                failed = "".join(f"\n{_failed_line(p, why)}" for p, why in missed.items())
                head = "⚠️ <b>Posted with problems</b>" if failed else "✅ <b>Published</b>"
                _notice(self, f"{head}\n\n<b>{html.escape(payload.get('title', tid))}</b>\n\n{links}{failed}",
                            section="needs" if failed else "info", key=f"posted:{run_id}", run=run_id, replace=True,
                             actions=self._episode_action(run_id) if failed else None)
        elif status == "simulated":
            self.db.update_run(run_id, "simulated")
            self.topic_engine.record_status(tid, previous_status, run_id=run_id)
        elif status == "blocked_unlicensed":
            self.db.update_run(run_id, "approved", error=pub.get("error"))
            self.topic_engine.record_status(tid, "approved", run_id=run_id)
            log.warning("Publishing halted for %s: commercial voice license required.", run_id)
            key = self.db.run_key(run_id)
            self._alert(
                f"🛑 <b>Blocked: unlicensed voice</b>\n\n"
                f"Topic: <b>{html.escape(payload.get('title', tid))}</b>\n"
                f"Voice used: <code>{html.escape(str(pub.get('provider', 'unlicensed')))}</code> (Unlicensed)\n\n"
                f"Meta Content Monetization Program (CMP) policies prohibit commercial publishing without a commercial license.\n\n"
                f"👉 <i>Options:</i>\n"
                f"• Configure an active Azure Speech key or ElevenLabs paid plan\n"
                f"• Or execute a simulated publish for testing",
                [
                    {"text": "🧪 Simulate Publish Only", "callback_data": f"simpub:{key}"},
                    {"text": "📨 Resend review card", "callback_data": f"resend:{key}"}
                ],
                group="needs",
            )
            self._stamp_review(run_id, status, pub)
            return {"status": status, "publish_result": pub}
        elif status == "unknown":
            self.db.update_run(run_id, "publish_unknown", error=pub.get("error"))
            self.topic_engine.record_status(tid, "publish_unknown", run_id=run_id)
            log.error("Publish outcome UNKNOWN for %s. Check the accounts, then `topic requeue %s` if it did not post.", run_id, tid)
            key = self.db.run_key(run_id)
            self._alert(
                f"⚠️ <b>Publish unconfirmed</b>\n\n<b>{html.escape(self.db.run_label(run_id))}</b>\n\n"
                f"The upload may have gone through ({html.escape(str(pub.get('error') or 'no confirmation received')[:200])}). "
                "I won't retry automatically, to avoid a duplicate post. Check Facebook and Instagram, then tell me:",
                [{"text": "✅ It posted", "callback_data": f"pubok:{key}"},
                 {"text": "🔁 It didn't post", "callback_data": f"pubno:{key}"}],
                group="needs",
            )
        else:
            self.db.update_run(run_id, "failed", error=pub.get("error"))
            self.topic_engine.record_status(tid, "failed", run_id=run_id)
            log.error("Publishing failed for %s: %s", run_id, pub.get("error"))
            self._alert(f"❌ <b>Publish failed</b>\n\n<b>{html.escape(self.db.run_label(run_id))}</b>\n<blockquote expandable>{html.escape(str(pub.get('error') or 'no error details were returned')[:500])}</blockquote>\nNothing was posted. The topic goes back in the queue for a retry.",
                        group="needs", key=f"fail:{run_id}", run=run_id,
                        actions=[{"text": "Details", "callback_data": f"chist:{self.db.run_key(run_id)}"}])
        if profile == "reel_9x16" and status == "published" and not dry_run:
            self._facebook_finishing(run_id)  # its cover and captions on Facebook, as soon as it is up
        if shorts and status in ("published", "simulated"):
            pub["youtube_short"] = short = self._timed_upload(
                run, lambda: self.publisher.publish_short(envelope, dry_run=dry_run))
            if short["status"] == "published":
                if receipt:
                    yt_urls = list(short.get("post_urls", {}).values())
                    receipt.update_platform("youtube", "live", url=yt_urls[0] if yt_urls else None)
                else:
                    links = "\n".join(html.escape(str(v)) for v in short.get("post_urls", {}).values())
                    _notice(self, f"▶️ <b>YouTube Short posted</b> ({html.escape(config.YOUTUBE_SHORTS_PRIVACY)})\n"
                                f"<b>{html.escape(payload.get('title', tid))}</b>\n{links}", section="info", run=run_id)
            elif short["status"] == "skipped" and receipt:   # no uploader set up: not attempted, said in plain words
                receipt.update_platform("youtube", "skipped", error="no account that can upload Shorts is set up")
            elif short["status"] in ("failed", "unknown"):
                if receipt:
                    receipt.update_platform("youtube", short["status"], error=str(short.get("error", "")))
                else:
                    # The Reel stands either way; an unknown Short is checked by hand, never re-sent blindly.
                    _notice(self, f"⚠️ <b>YouTube Short {'unconfirmed' if short['status'] == 'unknown' else 'not posted'}</b>\n"
                                f"<b>{html.escape(payload.get('title', tid))}</b>: the Reel is unaffected.\n"
                                f"{html.escape(str(short.get('error', ''))[:300])}", section="needs", run=run_id,
                                 actions=self._episode_action(run_id))
        if receipt and status == "published":
            # Its Stories and carousel as booked, never a fixed promise (it said "in 45 minutes" and "its next
            # slot" whether or not any was booked: UI-02); Posting has their times.
            booked = {p["kind"] for p in self.db.list_scheduled_posts(("pending", "waiting"), run_id=run_id)}
            follow = [name for kind, name in (("story", "Stories"), ("carousel", "carousel")) if kind in booked]
            receipt.finalize(follow_up=f"◽ Its {' and '.join(follow)} follow as booked (see Posting)." if follow else "")
        if dry_run and profile == "reel_9x16" and status == "simulated":
            # A live approval books the Stories and the carousel for their slots; a dry run skipped them
            # entirely, so it never tested them. They are simulated here, each request as it would be sent.
            run_dir = Path(run["master_video_path"]).parent
            from src.release import story_frames
            stories = [str(p) for p in story_frames(run_dir)]
            slides = sorted(str(p) for p in (run_dir / "carousel").glob("carousel_slide_*.jpg"))
            if stories:
                pub["stories"] = self.publisher.publish_photos(run_id, tid, "story", stories, dry_run=True)
            from src import carousel_lazy
            if carousel_lazy.planned(run_dir) and not slides:   # drawn only when it takes a real slot (QF-81)
                pub["carousel"] = {"status": "deferred", "reason": "drawn only when it takes a carousel slot"}
            if slides:
                try:
                    caption, comment = self._carousel_request(run_id, payload, slides)
                    pub["carousel"] = self.publisher.publish_photos(run_id, tid, "carousel", slides, caption=caption,
                                                                    dry_run=True, **({"first_comment": comment} if comment else {}))
                except ValueError as e:   # its caption is not the one the review card showed (QF-43)
                    pub["carousel"] = {"status": "failed", "error": str(e)}
        self._stamp_review(run_id, status, pub)
        return {"status": status, "publish_result": pub}

    def _stamp_review(self, run_id: str, status: str, pub: Dict[str, Any]) -> None:
        """The review card's receipt: what was approved and what actually happened (never fails publishing)."""
        try:
            if status == "simulated":
                done = ["Reel"] + (["YouTube Short"] if (pub.get("youtube_short") or {}).get("status") == "simulated" else [])
                if (pub.get("stories") or {}).get("status") == "simulated":
                    done.append(f"{pub['stories'].get('planned_parts', 0)} Story frame(s)")
                if (pub.get("carousel") or {}).get("status") == "simulated":
                    done.append("carousel" + (" (Instagram and Facebook copies)" if pub["carousel"].get("planned_parts", 0) > 1 else ""))
                text = "✅ <b>Approved · dry run</b>: nothing was posted. Simulated: " + ", ".join(done) + "."
                if (pub.get("carousel") or {}).get("status") == "deferred":
                    text += " The carousel is drawn only when it takes a carousel slot, so a dry run does not draw it."
            elif status == "published":
                urls = dict(pub.get("post_urls") or {})
                for url in ((pub.get("youtube_short") or {}).get("post_urls") or {}).values():
                    urls["youtube short"] = url
                problems = dict(_missing_reasons(pub))
                short = pub.get("youtube_short") or {}
                if short.get("status") in ("failed", "unknown"):
                    problems["youtube short"] = ("not confirmed: " if short["status"] == "unknown" else "") + str(short.get("error") or "")
                # The outcome as it is: a platform that failed is named, never folded into "posted" (UI-02)
                text = (("⚠️ <b>Approved · posted with problems</b>" if problems else "✅ <b>Approved · posted</b>")
                        + "".join(f"\n✅ {html.escape(_PLATFORM_NAMES.get(k, k))}: {html.escape(str(v))}" for k, v in urls.items())
                        + "".join(f"\n{_failed_line(k, why)}" for k, why in problems.items()))
            elif status == "unknown":
                text = ("⚠️ <b>Approved · publish unconfirmed</b>: check the accounts, then answer the "
                        "PUBLISH UNCONFIRMED alert (It posted / It didn't post).")  # it sits above the panel
            elif status == "blocked_unlicensed":
                text = "🛑 <b>Approved · blocked</b>: the voice has no commercial licence; nothing was posted."
            else:
                text = "❌ <b>Approved · not posted</b>: " + html.escape(str(pub.get("error") or status)[:300])
            self.telegram_bot.stamp_review_outcome(run_id, text)
        except Exception as e:
            log.debug("Could not stamp the review card for %s: %s", run_id, e)


def with_earlier_rejections(notes: str, earlier: List[str]) -> str:
    """This draft's findings, plus every earlier draft's, as claims never to restate. Each revision saw
    only the latest findings, and the same unsupported premise came back reworded in each draft until
    the run failed ("built before farming", production 2026-10-23)."""
    if not earlier:
        return notes
    history = "\n".join(f"- {n[:600]}" for n in earlier)
    return (notes + "\n\nRejected in earlier drafts of this script. Do not restate these claims or their idea in "
            "any wording, anywhere: title, caption, first comment, narration, on-screen text, slides or graphics. "
            "If the topic's framing depends on one, frame it with facts the research notes do support:\n" + history)


_undo_clock = time.time    # the Undo window's clock and wait (tests replace them)
_undo_sleep = time.sleep
UNDO_POLL_SECONDS = 2.0


def undone_before_acting(db, run_id: str, decision: str, bot=None) -> bool:
    """Claims the review's decision before acting on it (REVIEW_CLAIM_PREFIX: its decision time), then reads
    it again: True when the card's Undo reopened it first, so the wait goes on. The undo
    (db.undo_review_decision) refuses once the claim holds the decision's time; both are single writes, so
    exactly one of them wins (a V2 Undo reopened runs already published: UI-02).

    A decision tapped on a V2 review card is claimed only once its Undo window (config.REVIEW_UNDO_MINUTES) has
    passed, so the card's Undo works for the whole window it offers (it lasted one 5 s poll: review 1 of UI-02).
    The wait reads the decision again every UNDO_POLL_SECONDS meanwhile. On the claim the card's Undo button is
    removed (`bot.close_undo`)."""
    if not (hasattr(db, "get_approval") and hasattr(db, "set_setting")):
        return False
    approval = db.get_approval(run_id) or {}
    stamp = str(approval.get("decision_timestamp") or "")
    if approval.get("decision") != decision:
        return _reopened(approval)
    if not stamp:
        return False
    from src.telegram_bot import undo_until, v2_card_decision

    def changed(now: dict) -> bool:
        return now.get("decision") != decision or str(now.get("decision_timestamp") or "") != stamp

    v2 = v2_card_decision(db, run_id, approval)
    until = undo_until(approval) if v2 else None
    if until is not None and _undo_clock() < until:
        log.info("The %s of %s can be undone on its card until its window ends; acting after it.", decision, run_id)
    while until is not None and _undo_clock() < until:
        _undo_sleep(max(0.0, min(UNDO_POLL_SECONDS, until - _undo_clock())))
        now = db.get_approval(run_id) or {}
        if changed(now):
            log.info("The %s of %s was undone on its card before it was acted on; waiting again.", decision, run_id)
            return bool(now)
    db.set_setting(REVIEW_CLAIM_PREFIX + run_id, stamp)
    now = db.get_approval(run_id) or {}
    if changed(now) and _reopened(now):
        log.info("The %s of %s was undone on its card before it was acted on; waiting again.", decision, run_id)
        return True
    if v2 and bot is not None and hasattr(bot, "close_undo"):
        bot.close_undo(run_id)      # claimed: no Undo stays tappable
    return False


def slot_clock(db, run_id: str) -> datetime | None:
    """The time an approved Reel's slot is picked from: its decision's time when that was at most the Undo window and
    half an hour ago (the window was sized for a slot from then: _approval_window), otherwise None (now)."""
    try:
        decided = datetime.fromisoformat(str((db.get_approval(run_id) or {}).get("decision_timestamp")))
    except (TypeError, ValueError, AttributeError):
        return None
    if decided.tzinfo is None:
        return None
    age = datetime.fromtimestamp(_undo_clock()).astimezone() - decided
    return decided if timedelta(0) <= age <= timedelta(minutes=float(config.REVIEW_UNDO_MINUTES) + 30) else None


def _reopened(approval: dict[str, Any]) -> bool:
    """The review changed after the wait read its decision: an Undo reopened it, or it was decided again since."""
    return approval.get("decided_by") == UNDONE_BY or approval.get("decision") not in (None, "pending") and bool(approval)


_PLATFORM_NAMES = {"instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube",
                   "youtube short": "YouTube Short"}


def _missing_reasons(pub: dict[str, Any]) -> dict[str, str]:
    """{platform: why it was not posted} for each platform a published Reel missed (publisher.platform_errors,
    or the merged error for a result recorded before it)."""
    reasons = pub.get("platform_errors") or {}
    return {p: str(reasons.get(p) or pub.get("error") or "no reason was returned") for p in pub.get("missing_platforms") or []}


def _failed_line(platform: str, reason: str) -> str:
    """'❌ Instagram: <plain reason>' (HTML)."""
    from src.telegram_bot import plain_reason
    return f"❌ {html.escape(_PLATFORM_NAMES.get(platform, platform))}: {html.escape(plain_reason(reason))}"


def _receipt_platforms(requested: list[str], shorts: bool) -> list[str]:
    """The publish receipt's rows: what was attempted, in order (a documentary's YouTube video is "youtube-video";
    "youtube" is the Reel's Short, listed only when it will be attempted)."""
    rows = [p for p in ("instagram", "facebook") if p in requested]
    rows += ["youtube-video"] if "youtube" in requested else []
    return rows + (["youtube"] if shorts and rows and "youtube" not in requested else [])


def _receipt_outcomes(pub: dict[str, Any], requested: list[str]) -> dict[str, dict[str, Any]]:
    """The receipt's first state from a published result: each requested platform it did not miss is live (with
    its link when one was returned), each missed one failed with its reason."""
    urls, missed = pub.get("post_urls") or {}, _missing_reasons(pub)
    row = {"youtube": "youtube-video"}
    out = {row.get(p, p): {"status": "live", "url": urls.get(p) if isinstance(urls.get(p), str) else None}
           for p in requested if p not in missed}
    out.update({row.get(p, p): {"status": "failed", "error": why} for p, why in missed.items()})
    return out


def _write_result(path: Optional[str], result: Dict[str, Any]) -> None:
    """The worker's outcome for the daemon that started it (never fails the run)."""
    if not path:
        return
    try:
        Path(path).write_text(json.dumps(result, default=str), encoding="utf-8")
    except OSError as e:
        log.warning("Could not write the run result to %s: %s", path, e)


def proposal_notice(bot, db, proposal: dict[str, Any]) -> None:
    """A ready improvement proposal for the operator. The clean chat makes the night's proposals one item,
    "🧭 3 improvement proposals" with 🧭 Improvements (design §6.1.3: the 29 Oct three-card complaint); otherwise
    each is its own card with its buttons."""
    from src import improve
    if not config.TELEGRAM_CLEAN_CHAT:
        text, buttons = improve.card(proposal)
        bot.notify(text, buttons=buttons, silent=True, record_alert=False, group="needs")
        return
    # The proposals item is a condition item (UI-01b): it follows the ledger, and resolves itself once every
    # proposal is decided.
    from src import notice_conditions
    notice_conditions.sync(db)
    kick = getattr(getattr(bot, "layout", None), "kick", None)
    if callable(kick):
        kick()


POLL_BACKOFF_SECONDS, POLL_BACKOFF_MAX = 10, 120


def poll_loop(bot, handler) -> None:
    """Polls Telegram until interrupted. A transport failure (a timeout, a 5xx, a dropped connection) is
    routine on a long poll: one line and a growing wait, not a traceback (hundreds of them buried the real
    errors in both logs, 2026-10-24). Anything else is still logged in full."""
    from src.control import short_reason
    from src.telegram_bot import transport_error
    backoff = POLL_BACKOFF_SECONDS
    while True:
        try:
            bot.poll_once(handler)
            backoff = POLL_BACKOFF_SECONDS
        except KeyboardInterrupt:
            raise
        except BaseException as e:
            if transport_error(e):
                log.warning("Telegram unreachable (%s); polling again in %d s.", short_reason(str(e), 160), backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, POLL_BACKOFF_MAX)
            else:
                log.exception("Telegram polling error (will retry): %s", e)
                time.sleep(POLL_BACKOFF_SECONDS)


REMAKE_STATUSES = ("failed", "timed_out", "rejected", "regenerate")  # runs with a video that Re-render makes again without paying twice
PROMOTABLE_STATUSES = ("simulated", "approved")  # a finished dry run that "Post for real" may publish
PROMOTED_PREFIX = "promoted:"  # setting: the run was promoted to real posting (and when)
# Whether the posting process posts only promoted runs (a dry-run daemon or bot), so Posting and Slots show what
# will really go out (src/posting_view.py, as _may_post decides).
POSTS_ONLY_PROMOTED_KEY = "posts_only_promoted"


def record_posting_mode(coordinator) -> None:
    """Keeps whether this process posts only promoted runs, for Posting and Slots; never stops a start."""
    try:
        coordinator.db.set_setting(POSTS_ONLY_PROMOTED_KEY, "true" if coordinator.only_promoted else "false")
    except Exception as e:
        log.debug("Posting mode not recorded: %s", e)


def graph_reason(error: Exception) -> str:
    """Facebook's own sentence from a Graph error ("(#100) Invalid file. ..."), without the request URL and the
    rest of its reply; the whole error when it has no such sentence."""
    text = str(error)
    found = re.search(r"""['"]message['"]:\s*['"](.+?)['"],\s*['"](?:type|code)['"]""", text)
    return (found[1] if found else text)[:300]


LAYOUT_HELD_DIR = "held"   # a held deck's frames, under its own folder: out of the posting globs (non-recursive)


def hold_layout_error(future, run_dir: Path, folder: str, payload: dict[str, Any]) -> list[Path]:
    """`future`'s slides, or [] when the deck raised typography.LayoutError (QF-23: its checked copy fits no
    permitted layout whole): that deck alone is held. The error is logged, the frames it had saved are moved to
    <folder>/held/ (the posting globs and the Facebook copy's file check then find none), and payload["layout_holds"]
    records it for the review card. Any other error propagates."""
    from src.typography import LayoutError
    try:
        return future.result()
    except LayoutError as e:
        log.error("%s held: its text cannot be set whole (%s); the run goes on without it.", folder, e)
        src = Path(run_dir) / folder
        moved = []
        if src.is_dir():
            dst = src / LAYOUT_HELD_DIR
            dst.mkdir(exist_ok=True)
            for p in sorted(src.glob("*.jpg")):
                shutil.move(str(p), str(dst / p.name))
                moved.append(p.name)
        payload.setdefault("layout_holds", []).append({"deck": folder, "error": str(e), "moved": moved})
        return []


def prepared_still(path):
    """A still as the video uses it: upscaled when low-resolution, then gently colour and brightness balanced
    (src/colour_balance.py). Faces are de-identified after this, by the caller."""
    return colour_balance.balanced(ensure_resolution(path))


def beat_of_line(beats: List[Dict[str, Any]], line: str) -> Optional[int]:
    """The beat (1-based) that says most of `line`'s words (a narration sentence; beats are cut by time, not by
    sentence), None when none says any."""
    words = set(re.findall(r"[a-z0-9']+", (line or "").lower()))
    if not words:
        return None
    best, at = 0, None
    for i, b in enumerate(beats or [], 1):
        n = len(words & set(re.findall(r"[a-z0-9']+", str(b.get("text") or "").lower())))
        if n > best:
            best, at = n, i
    return at


def beat_where(beat: int, beats: List[Dict[str, Any]]) -> str:
    """Where a video-check finding is: "beat 6, 0:21-0:26" (the failure card gave only its sentence)."""
    if beat == 0:
        return "cover"
    if not 1 <= beat <= len(beats or []):
        return f"beat {beat}"
    b = beats[beat - 1]
    clock = lambda s: f"{int(float(s) // 60)}:{int(float(s) % 60):02d}"  # noqa: E731
    return f"beat {beat}, {clock(b['start'])}-{clock(b['end'])}"


def _reuse_options(p) -> None:
    from src.script_library import STEPS
    p.add_argument("--reuse-from", metavar="RUN",
                   help="Reuse what this earlier run of the same topic made and passed (script, voice, music, clips)")
    p.add_argument("--fresh-from", choices=STEPS,
                   help="Make this step and every later one again: nothing from the script library or an earlier run")


def apply_reuse_options(coordinator, args) -> Optional[str]:
    """Sets up --reuse-from and --fresh-from for this run; returns why the run cannot start, if it cannot."""
    from src.script_library import set_fresh_from
    set_fresh_from(getattr(args, "fresh_from", None))
    reuse = getattr(args, "reuse_from", None)
    coordinator.reuse_from = None
    if not reuse:
        return None
    reuse = coordinator.db.resolve_run(reuse) or reuse
    topic = regeneration_topic(coordinator.db, reuse)
    if not topic or not (OUTPUT_DIR / reuse).is_dir():
        return f"Run '{reuse}' is not recorded or its files are no longer kept."
    if args.topic_id and args.topic_id != topic:
        return f"Run '{reuse}' was made for {topic}, not {args.topic_id}: only the same topic's work is reused."
    args.topic_id, coordinator.reuse_from = topic, reuse
    return None


def regeneration_topic(db, previous: str) -> Optional[str]:
    """The topic a regeneration is for: the one the run it replaces was made for."""
    return (db.get_run(previous) or {}).get("topic_id")


_WORDS_ABOUT_SCRIPT = ("narration", "voiceover", "voice over", "script", "phrase", "wording", "word", "sentence",
                       "repeat", "repeated", "caption", "text", "story", "stories", "carousel", "spelling", "typo")


def revises_script(fb: Dict[str, Any]) -> bool:
    """A review of reason "other" whose own words are about what is said or written is treated as a review
    of the words (operator, 2026-10-25: "Incomplete text on a Story image and repeated phrases in the
    voiceover" under Other kept the script word for word)."""
    if fb.get("reason") != "other" or not fb.get("text"):
        return False
    words = set(re.findall(r"[a-z]+", str(fb["text"]).lower()))
    return any(w in words for w in _WORDS_ABOUT_SCRIPT) or "voice over" in str(fb["text"]).lower()


def gate_problems(db, run_id: str, dry_run: Optional[bool] = None) -> List[str]:
    """What the release gate blocks in this run as it stands, one line per finding ([] when it passes): the
    check the Telegram card applies (no Approve on a blocked version), for approvals made elsewhere."""
    from src.release import release_problems, write_release_report
    run = db.get_run(run_id) or {}
    if not run.get("master_video_path"):
        return []   # nothing rendered: publish_run refuses it with its own reason
    if dry_run is None:  # the mode it would post in: production has no dry run, whatever the run was made as (review 2026-10-28)
        dry_run = resume_mode(db, run_id, False)
    return release_problems(write_release_report(run, dry_run=dry_run), dry_run)


def reason_window(db, run_id: str, seconds: float, least: float = 10) -> float:
    """What is left of a reason window counted from the Regenerate tap, at least `least` seconds: the bot's
    worker starts `run --regenerate-from` seconds after the tap, and a fixed 10 s missed the words that came
    later (2026-10-26); a regeneration started long after (a resend, a restart) waits only briefly."""
    try:
        decided = datetime.fromisoformat(str((db.get_approval(run_id) or {}).get("decision_timestamp")))
        elapsed = (datetime.now(timezone.utc) - decided).total_seconds()
    except (AttributeError, TypeError, ValueError):   # no record of the tap: the short wait
        return least
    return max(least, seconds - elapsed)


def regeneration_plan(fb: Dict[str, Any]) -> str:
    """What a regeneration keeps and redoes, by the same rules _produce_and_publish applies (HTML, one or two
    sentences for the decided card: the operator could not tell the script would be kept, 2026-10-26)."""
    from src.script_library import reuses
    reason = fb.get("reason")
    if not reason:
        return "No reason came, so it is made fresh: a <b>new script</b>, voiceover and music."
    if reason in SCRIPT_REASONS or revises_script(fb):
        return "Redoes the <b>script</b>, revised for what you said (so a new voiceover and music bed follow)."
    if not reuses("script"):
        return "It is made fresh from the script step (this run was told to), so nothing is carried over."
    from src import prevoice_lines
    if reason == "pictures" and prevoice_lines.enabled():
        # Said as it is done (2026-10-29): a line no picture shows may be reworded before the voice
        # (_lines_against_pictures), so the voice is kept only when no line needs that.
        return ("Keeps the <b>script word for word</b> (if it still passes today's checks). Redoes the <b>pictures</b>: "
                "they are re-planned, and photos are searched for again (Commons, then open museum archives) for any "
                "line no picture shows. Only a line that still has none is reworded, with the same verified facts and "
                "fact-checked again; then the voiceover is recorded again and a new music bed follows. Otherwise its "
                "voiceover and music bed carry over.")
    redo = {"pictures": " Redoes the <b>pictures</b>: they are re-planned.",
            "sound": " Redoes the <b>music</b>: a different track is used.",
            "voice": " The voice is noted for improvement; it is not changed automatically.",
            "other": " Your words are noted for improvement; a script, voiceover or caption problem needs "
                     "them to say so (for example \"the narration repeats\")."}.get(reason, "")
    return ("Keeps the <b>script word for word</b> (if it still passes today's checks), so its voiceover"
            + (" carries" if reason == "sound" else " and music bed carry") + " over." + redo)


BATCH_STARTED_KEY = "produce_started_at"
BATCH_MAX_HOURS = 24  # a /produce batch is for now: one left over for days is dropped, not continued


def batch_expired(db) -> bool:
    """Whether the /produce batch in the settings is older than BATCH_MAX_HOURS (or has no start recorded: a
    batch from before 2026-10-28 is treated as expired)."""
    try:
        started = datetime.fromisoformat(db.get_setting(BATCH_STARTED_KEY) or "")
    except (TypeError, ValueError, AttributeError):
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - started > timedelta(hours=BATCH_MAX_HOURS)


def resume_mode(db, run_id: str, dry_run: bool) -> bool:
    """The mode a resumed run continues in. In development, as it was made (a dry run never posts by
    surprise). In production there is no dry run (operator, 2026-10-25): a run made before the activation
    continues live, so its approval books it like any other; the record says so."""
    made_dry = db.get_setting("run_mode:" + run_id) == "dry_run"
    if dry_run or not made_dry:
        return dry_run or made_dry
    if config.BACKUP_ENVIRONMENT == "production":
        db.set_setting("run_mode:" + run_id, "live")
        log.info("Run %s was made as a dry run; production has none now, so it continues live.", run_id)
        return False
    return True


def rerender_started(db, run_id: str) -> str:
    """What a Re-render keeps, from the run's own records (src/resume_points.py), not a fixed sentence."""
    from src.resume_points import kept_phrase
    run = db.get_run(run_id) or {}
    if run.get("status") in ("rejected", "regenerate"):
        return (f"🔁 <b>{html.escape(db.run_label(run_id))}</b> is being made again with the reason you gave, keeping "
                "its voiceover and accepted motion clips. Its progress shows here, and a new review card comes when it's ready.")
    kept = kept_phrase(OUTPUT_DIR / run_id, run) or "what it had made"
    return (f"🔁 <b>{html.escape(db.run_label(run_id))}</b> is being re-rendered, keeping {html.escape(kept)} "
            "(a stage whose inputs changed, or a voiceover outside the length limits, is made again). Its progress shows here.")


def rerender_reply(coordinator, run_id: str, launch) -> "notifications.Outcome":
    """Re-render's reply with its outcome (notifications.Outcome): refused, a run already in progress, or started.
    `launch(extra)` starts it and returns an Outcome that is not done when it could not start. The outcome is the
    handler's own, never read from the reply's words (a topic called "Notre-Dame" is not a refusal)."""
    refusal = coordinator.rerender_refusal(run_id)
    if refusal:
        return notifications.Outcome(f"🔁 {html.escape(refusal)}", done=False)
    started = launch(coordinator.remake_args(run_id))
    if getattr(started, "done", True) is False:
        return started
    return notifications.Outcome(rerender_started(coordinator.db, run_id), done=True)


UPDATES_BUSY = "A production run is in progress; try again when it finishes."
UPDATES_STARTED = "🧰 Applying updates on a branch and running the tests. I'll report back."
UPDATES_ITEM_KEY = "maintenance:apply"   # the Needs-you item offering 🧰 Apply updates


def apply_updates_reply(busy: bool, start) -> "notifications.Outcome":
    """🧰 Apply updates' reply with its outcome: refused in production or while a production run is in progress
    (its item stays open, saying why), else started in the background."""
    from src import maintenance
    if not maintenance.apply_allowed():  # an old Apply button pressed in production
        return notifications.Outcome(html.escape(maintenance.PRODUCTION_REFUSAL), done=False)
    if busy:
        return notifications.Outcome(UPDATES_BUSY, done=False)
    start()
    return notifications.Outcome(UPDATES_STARTED, done=True)


def say_update_report(bot, plan) -> None:
    """The weekly update report (design §8: every sender writes into the notifications panel, never a message).
    With something to apply outside production it is a Needs-you item whose action is 🧰 Apply updates; otherwise an
    info item until next week's, and an Apply item still open is resolved (nothing is left to apply)."""
    from src import maintenance
    buttons = maintenance.apply_buttons(plan)  # none in production: updates arrive as a signed release
    if buttons:
        notifications.say(bot.notify, maintenance.report_text(plan), buttons=[buttons], group="needs",
                          key=UPDATES_ITEM_KEY, replace=True, record_alert=False)
        return
    if config.TELEGRAM_CLEAN_CHAT:
        notifications.close_key(bot.db, UPDATES_ITEM_KEY)
    bot.notify(maintenance.report_text(plan), transient=True, ttl_minutes=10070, group="info",  # until next week's
               key="maintenance:weekly")



def run_in_worker(content_format: str, dry_run: bool, schedule_posts: bool, timeout: Optional[float] = None,
                  extra: Optional[List[str]] = None, resume: Optional[str] = None, db=None) -> Dict[str, Any]:
    """Runs one production cycle in its own process (main.py run), as the development bot already does.
    A native crash in a run (2026-10-23: a GPU library brought a worker down) then costs that run only,
    not the daemon with its bot and schedule. The daemon's poller keeps handling Telegram; the worker
    reads decisions from the database (--background-polling). Returns the worker's result, with
    "crashed" (and the exit code) when it ended without writing one, and "timed_out" with stopped=True when
    its production outran the deadline plus its grace without stopping itself (src/worker_deadline.py):
    `timeout` seconds of production, review waits excluded (default: config.run_deadline_limits)."""
    import tempfile
    import uuid
    limit = config.run_deadline_limits()[1] if timeout is None else timeout
    if not 0 < limit < float("inf"):
        raise ValueError("The worker's limit must be a positive number of seconds")
    attempt = uuid.uuid4().hex  # the runs this worker claims carry it (claim_run); nothing else is ever stopped
    fd, result_path = tempfile.mkstemp(prefix="rr-run-", suffix=".json")
    os.close(fd)
    # `resume`: re-render that failed run in place (its script, voice and clips are reused).
    command = ["resume", resume, "--require-live"] if resume else ["run", "--require-live", "--format", content_format]
    cmd = ([sys.executable, "-u", "main.py", *command, "--background-polling", "--result-file", result_path]
           + (["--dry-run"] if dry_run else []) + (["--schedule-posts"] if schedule_posts else []) + list(extra or []))
    try:
        proc = subprocess.Popen(cmd, cwd=str(config.BASE_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace",
                                env=dict(os.environ, RELIC_WORKER_ATTEMPT=attempt), **worker_deadline.launch_options())
        try:
            _, stderr, stopped = worker_deadline.supervise(proc, limit, db, attempt)
        except BaseException:  # never leave an unsupervised worker behind
            worker_deadline.stop_worker_tree(proc)
            raise
        if stopped:
            return {"status": "timed_out", "stopped": True, "attempt": attempt, "exit_code": proc.returncode}
        try:
            result = json.loads(Path(result_path).read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            result = {}
        if not result:  # no result written: the process died (a native crash ends it without one)
            result = {"status": "crashed", "exit_code": proc.returncode,
                      "stderr_tail": "\n".join((stderr or "").strip().splitlines()[-8:])}
        result["exit_code"] = proc.returncode
        return result
    finally:
        Path(result_path).unlink(missing_ok=True)


@contextlib.contextmanager
def busy_lease(db: PipelineDatabase, job: str):
    """Marks a daemon job as running, so the release deployer (scripts/release_gate.py) never stops
    production in the middle of it. A lease left by a process that died is cleared at daemon startup."""
    key = "busy:" + job
    try:
        db.set_setting(key, json.dumps({"pid": os.getpid(), "started": datetime.now(timezone.utc).isoformat()}))
    except Exception as e:
        log.debug("Could not record the %s lease: %s", job, e)
    try:
        yield
    finally:
        try:
            db.set_setting(key, "")
        except Exception as e:
            log.debug("Could not clear the %s lease: %s", job, e)


def clear_busy_leases(db: PipelineDatabase) -> None:
    with db._conn() as conn:
        conn.execute("UPDATE settings SET value = '' WHERE key LIKE 'busy:%'")


RELEASE_RECEIPT_ENV = "RELIC_RELEASE_RECEIPT"  # set by the production launcher; written by the release deployer


def announce_release(db: PipelineDatabase, bot, startup: bool = False) -> Optional[str]:
    """Tells the operator once what the release deployer did, from its receipt. At startup after a release
    was installed or rolled back (the daemon has just restarted on it), the chat is cleared the way /clear
    does (open reviews with their media and the live progress card stay) and the notice is returned for
    the top of the one dashboard panel; otherwise (a release refused while running) it is sent as a notice."""
    path = os.environ.get(RELEASE_RECEIPT_ENV)
    if not path:
        return None
    try:
        receipt = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    key = f"{receipt.get('tag')}:{receipt.get('outcome')}:{receipt.get('at')}"
    if db.get_setting("release_announced") == key:
        return None
    tag, running = html.escape(str(receipt.get("tag"))), html.escape(str(receipt.get("running") or ""))
    if receipt.get("outcome") == "deployed":
        text = (f"🚀 <b>Release {tag} is running</b> (was {html.escape(str(receipt.get('previous') or 'unknown'))}). "
                "It was signed, passed the tests on this machine and was installed while production was idle.")
    elif receipt.get("outcome") in ("rolled_back", "rejected"):
        where = "is back on" if receipt.get("outcome") == "rolled_back" else "stays on"
        text = (f"⚠️ <b>Release {tag} was not installed</b>: {html.escape(str(receipt.get('reason') or 'its checks failed')[:400])}. "
                f"Production {where} {running}; the release won't be retried.")
    else:
        return None
    db.set_setting("release_announced", key)
    if startup:  # the restart that installed it ended any job that was running: say which, not silently
        try:
            from src.operator_jobs import Jobs
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            lost = [j["title"] for j in Jobs(db).recent() if j["state"] == "interrupted" and str(j.get("updated_at", "")) >= cutoff]
            if lost:
                text += ("\n⏹ Interrupted by the restart: " + ", ".join(html.escape(t) for t in lost)
                         + ". Start them again from their screens.")
        except Exception as e:
            log.debug("Could not list interrupted jobs: %s", e)
    if startup and receipt.get("outcome") in ("deployed", "rolled_back") and hasattr(bot, "clear_chat"):
        try:
            bot.clear_chat()
        except Exception as e:  # a clear never stops the daemon starting
            log.warning("Could not clear the chat after the release: %s", e)
        return text
    bot.notify(text, group="info", key="policy:weekly")
    return None


INTERRUPTED_GRACE_SECONDS = 30 * 60  # a run this young may still be starting (no progress record yet)


def reconcile_interrupted_runs(db: PipelineDatabase, topic_engine=None) -> List[str]:
    """At startup: runs still recorded as being made whose process has ended (a crash, a stopped process)
    are marked failed as interrupted, their topic goes back to the queue and their progress record is
    cleared. A crashed dev worker left its run "producing", which blocked the next deployment stop; in
    production it would also have kept a dead run's progress on the dashboard. A run whose process is
    alive or can't be checked, or one too young to have a progress record, is left alone."""
    from src.process_state import identity
    try:
        record = json.loads(db.get_setting("active_progress") or "{}")
    except ValueError:
        record = {}
    with db._conn() as conn:
        rows = conn.execute("SELECT run_id, topic_id, updated_at FROM runs WHERE status = 'producing'").fetchall()
    done = []
    for row in rows:
        run_id = row["run_id"]
        if record.get("run_id") == run_id and record.get("owner_pid"):
            observed = identity(record["owner_pid"])
            if observed is not None and (observed == "unknown" or observed == record.get("owner_identity")):
                continue  # its process is alive, or can't be checked: never declared dead on a guess
        else:
            try:
                started = datetime.fromisoformat(row["updated_at"])
                started = started if started.tzinfo else started.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if (datetime.now(timezone.utc) - started).total_seconds() < INTERRUPTED_GRACE_SECONDS:
                continue
        from src.telegram_bot import INTERRUPTED_ERROR
        db.update_run(run_id, "failed", error=INTERRUPTED_ERROR)
        db.clear_active_progress(run_id)
        if topic_engine is not None and row["topic_id"]:
            topic_engine.record_status(row["topic_id"], "queued", run_id=run_id)
        worker_deadline.forget(db, run_id)  # its process cannot release it any more (QF-74)
        log.warning("Run %s was interrupted (its process ended); marked failed, topic back in the queue.", run_id)
        done.append(run_id)
    return done


RUN_OWNER_PREFIX = "run_owner:"


def claim_run(db, run_id: str) -> None:
    """Records this process (pid and creation identity) as the one making run_id, until it releases it or
    ends. Another process never resumes a review wait for a run a live process owns (resume_reviews)."""
    from src.process_state import identity
    worker_deadline.mark_started(run_id)  # its production deadline counts from here (src/worker_deadline.py)
    now = datetime.now(timezone.utc).isoformat()
    try:
        # attempt: the supervisor that launched this worker finds its runs by it (worker_deadline.owned_runs)
        db.set_setting(RUN_OWNER_PREFIX + run_id, json.dumps({"pid": os.getpid(), "identity": identity(os.getpid()),
                                                              "attempt": config.WORKER_ATTEMPT_ID, "at": now}))
        db.set_setting(worker_deadline.STARTED_PREFIX + run_id, now)  # the slot it was made for (missed_slot_notice)
    except Exception as e:  # never fails the run; the review lock still guards the act itself
        log.warning("Could not record the owner of %s: %s", run_id, e)


def release_run(db, run_id: str) -> None:
    """Clears the owner record, only when it is this process's."""
    try:
        record = json.loads(db.get_setting(RUN_OWNER_PREFIX + run_id) or "{}")
        if record.get("pid") == os.getpid():
            db.set_setting(RUN_OWNER_PREFIX + run_id, "")
            worker_deadline.release(db, run_id)  # its deadline bookkeeping ends with the run (QF-74)
    except Exception as e:
        log.debug("Could not release the owner of %s: %s", run_id, e)


def _claimed_before_boot(record: dict) -> bool:
    """Whether an owner record was written before this machine last started. After a reboot a dead owner's pid
    is often reused by a service process this account may not open, which identity() reports as "unknown"
    (alive): without this its review was never resumed again. False when it can't be told."""
    try:
        claimed = datetime.fromisoformat(str(record["at"]))
        claimed = claimed if claimed.tzinfo else claimed.replace(tzinfo=timezone.utc)
        if os.name == "nt":
            import ctypes
            tick = ctypes.WinDLL("kernel32").GetTickCount64
            tick.restype = ctypes.c_ulonglong
            uptime = tick() / 1000.0
        else:
            uptime = float(Path("/proc/uptime").read_text().split()[0])
    except Exception:
        return False
    return claimed.timestamp() < time.time() - uptime - 60  # a minute's margin for clock adjustment


def run_owned_elsewhere(db, run_id: str) -> Optional[int]:
    """The pid of another live process that owns run_id, else None. A process that can't be checked counts
    as alive (never declared dead on a guess); a pid reused by a new process (other identity) does not."""
    from src.process_state import identity
    try:
        record = json.loads(db.get_setting(RUN_OWNER_PREFIX + run_id) or "{}")
    except (TypeError, ValueError):
        return None
    pid = record.get("pid")
    if not isinstance(pid, int) or pid == os.getpid():
        return None
    if _claimed_before_boot(record):  # its process ended with the last boot, whoever has the pid now
        return None
    observed = identity(pid)
    if observed is None:
        return None
    if observed == "unknown" or record.get("identity") in (None, "unknown") or observed == record.get("identity"):
        return pid
    return None


def resume_reviews(coordinator, follow, waiting: Optional[set] = None, decided_only: bool = False) -> List[str]:
    """Every rendered run still awaiting its decision gets a wait, without re-sending the card: at startup
    all of them, and then periodically those already decided (a decision made from the command line, or
    any that nothing acted on). A review card's buttons act only while something waits on them: after a
    restart a tapped Regenerate was recorded and nothing acted on it (dev, 2026-10-23). Runs this process
    already waits on (`waiting`) are skipped; callers never sweep while a production worker is alive,
    since that worker may be the one waiting."""
    query = ("SELECT r.run_id FROM runs r JOIN approvals a ON a.run_id = r.run_id WHERE r.status = 'pending_approval'"
             + (" AND a.decision NOT IN ('pending', 'timeout')" if decided_only else ""))
    try:
        with coordinator.db._conn() as conn:
            runs = [r["run_id"] for r in conn.execute(query) if r["run_id"] not in (waiting or set())]
    except Exception as e:
        log.warning("Could not resume review waits: %s", e)
        return []
    # A run another live process is making or finishing is its own: the CLI's auto-approved dry run was
    # still acting on its Approve (status pending_approval) when the bot resumed its wait and published it
    # a second time (dev, 2026-10-29 06:19, Richat). In production that would be a second post.
    owned = {r: run_owned_elsewhere(coordinator.db, r) for r in runs}
    for run_id, pid in owned.items():
        if pid:
            log.info("Not resuming the review wait for %s: process %s owns it.", run_id, pid)
    runs = [r for r in runs if not owned[r]]
    for run_id in runs:
        log.info("Resuming the review wait for %s.", run_id)
        follow(run_id, resend=False)
    return runs


def _report_interrupted(coordinator, bot) -> None:
    import sqlite3
    try:   # once per database: the runs made before music_selections existed (QF-84), so their publications count
        filled = coordinator.db.backfill_music_selections(OUTPUT_DIR)
        if filled:
            log.info("Music selections recorded for %d earlier run(s).", filled)
    except (OSError, ValueError, AttributeError, sqlite3.Error) as e:   # AttributeError: a stand-in database
        log.warning("Could not record the earlier runs' music selections: %s", e)
    try:
        stale = reconcile_interrupted_runs(coordinator.db, coordinator.topic_engine)
    except Exception as e:
        log.warning("Could not reconcile interrupted runs: %s", e)
        return
    if stale and config.TELEGRAM_CLEAN_CHAT:
        say_interrupted(coordinator.db, bot, stale)
    elif stale:
        bot.notify("⚠️ <b>Interrupted run(s) cleared</b>: the process making them ended before they finished. "
                   "Nothing was posted; the topics are back in the queue.\n"
                   + "\n".join(f"• {html.escape(coordinator.db.run_label(r))}" for r in stale), group="needs")


def _notice(owner, text: str, **item: Any) -> None:
    """A coordinator's notice with its clean-chat item fields (section, key, run, replace, actions: UI-01), through
    its _alert. A stand-in owner or _alert (tests use a namespace holding a list's append) gets what it takes."""
    notifications.say(owner._alert, text, **item)


def say_interrupted(db, bot, runs: List[str], why: str = "") -> None:
    """The clean chat (UI-01): each run whose process ended before it finished is the Needs-you item "⚠️ Run
    interrupted · <run>" under its failure key fail:<run>, so a worker crash, the reaper and the start-up clearing
    are one item for one run, never three (design §5.6, §10.2)."""
    for run_id in runs:
        notifications.say(bot.notify, f"⚠️ <b>Run interrupted</b> · {html.escape(db.run_label(run_id))}\n\n"
                        "The process making it ended before it finished. Nothing was posted; the topic is back in "
                        "the queue." + (f" {why}" if why else ""),
            group="needs", key=f"fail:{run_id}", run=run_id, replace=True,
            actions=notifications.failure_actions(db, run_id))


def prebuild_candidates(db: PipelineDatabase, n: int) -> list:
    """The next n topics production would pick (queued first) that have approved images."""
    from src.visual_engine import MissingAssetsError, VisualEngine
    out, ve = [], VisualEngine()
    try:  # the order production really picks in (it used a simpler sort, so it could prepare other topics)
        ordered = db.ranked_topics()
    except QueueEmpty:
        return []
    for t in ordered:
        if t["status"] not in ("queued", "failed"):
            continue
        try:
            ve.get_scene_sequence(t)
        except MissingAssetsError:
            continue
        out.append(t)
        if len(out) >= n:
            break
    return out


def new_version(coordinator: "PipelineCoordinator", run_id: str, rerender, busy=lambda: False) -> str:
    """♻️ Make a new version of an episode whose booking was blocked (QF-110): its bookings are cancelled and it is
    remade from this run (Re-render), then it comes to review like any new version. Every refusal comes first: while
    a run is being made (`busy`) nothing is cancelled."""
    if busy():
        return "♻️ A run is in progress; make the new version when it has finished (nothing was changed)."
    refusal = coordinator.prepare_new_version(run_id)
    return f"♻️ {html.escape(refusal)}" if refusal else rerender(run_id)


def _error_words(error: Exception) -> str:
    """An error as said to the operator and compared for "said once": its type and first line, with object
    addresses, long numbers and ids taken out (a network error carries a new address every minute)."""
    text = str(error).strip().splitlines()[0] if str(error).strip() else ""
    text = re.sub(r"0x[0-9a-fA-F]+", "0x…", text)
    text = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "…", text)
    text = re.sub(r"\b\d{5,}\b", "…", text)
    return f"{type(error).__name__}: {text}"[:300]


POSTING_ERROR_KEY = "posting_error_said"


def say_posting_error(db, bot, error: Exception | None) -> None:
    """A posting pass's error is said once, and again only when it changes or after a pass that worked (QF-110:
    one blocked Reel was reported about once a minute for days). `error` None: the pass worked."""
    said = "" if error is None else _error_words(error)
    try:
        if (db.get_setting(POSTING_ERROR_KEY) or "") == said:
            return
        db.set_setting(POSTING_ERROR_KEY, said)
    except Exception as e:  # noqa: BLE001 - a database that cannot be read still gets the message said
        log.debug("Posting error state not kept: %s", e)
    if error is not None:
        bot.notify(f"⚠️ Scheduled posting hit an error: {html.escape(str(error)[:300])}\n\nIt is tried again each "
                   "minute; you'll hear again only if the error changes.", group="needs", key="posting-error",
                   actions=[{"text": "🗓️ Posting", "callback_data": "cposts:"}])


def start_post_ticker(coordinator: "PipelineCoordinator", bot) -> threading.Thread:
    """Bot mode's scheduled posting: every minute, publish what is due (held while paused),
    as the daemon's post_due job does."""
    def tick():
        while True:
            time.sleep(60)
            if is_paused(coordinator.db):
                continue
            try:
                coordinator.post_due()
                say_posting_error(coordinator.db, bot, None)
            except Exception as e:
                log.exception("Posting scheduled items failed: %s", e)
                say_posting_error(coordinator.db, bot, e)

    thread = threading.Thread(target=tick, name="post-due", daemon=True)
    thread.start()
    return thread


def _proposal_trigger(db: PipelineDatabase, bot, n: int | None = None) -> Any:
    """Research is an operator job: cancellable and tied to its originating panel. `n`: the ideas it proposes
    (propose()'s default when None)."""
    def work():
        from src import topic_proposer
        proposals = topic_proposer.propose(db) if n is None else topic_proposer.propose(db, n)
        text = (f"💡 Research complete: {len(proposals)} new idea(s). Open Waiting ideas to review them."
                if proposals else "💡 No new verified topic ideas this time.")
        return text, [[{"text": "Waiting ideas", "callback_data": "cprop:"}],
                      [{"text": "← Topic ideas", "callback_data": "cideas:"}],
                      [{"text": "🏠 Dashboard", "callback_data": "cdash:"}]]

    def trigger() -> str:
        bot.start_operator_job("Researching topic ideas", work, resource="topic-research", late=True)
        return ""

    trigger.run_now = trigger
    return trigger


def _print_health(res: Dict[str, Any]):
    for k, v in res["details"].items():
        icon = "✓" if v["passed"] else ("✗" if v["required"] else "!")
        print(f"  [{icon}] {k.upper()}: {v['msg']}")
    print(f"Overall: {'READY' if res['all_passed'] else 'ATTENTION REQUIRED'}")


def main(argv=None) -> int:
    if (config.BASE_DIR / ".restore-isolated").exists():
        print("This is an isolated recovery copy. Runtime commands are disabled until recovery review is complete.")
        return 2
    setup_logging()
    parser = argparse.ArgumentParser(description="Relic & Riddle: Reels research, production & publishing pipeline")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Produce (and, once approved, publish) one Reel")
    run_p.add_argument("--topic-id")
    run_p.add_argument("--auto-approve", action="store_true", help="Skip human review (records who approved)")
    run_p.add_argument("--dry-run", action="store_true", help="Do everything except publish")
    run_p.add_argument(
        "--format",
        choices=["reel", "deep-dive", "dual"],
        default="reel",
        help="Format to produce: 'reel' (9:16 vertical), 'deep-dive' (16:9 widescreen), or 'dual' (both)",
    )
    run_p.add_argument("--require-live", action="store_true", help="Enforce production preflight credentials check")
    run_p.add_argument("--run-id", help="Re-use existing run directory to re-render without re-synthesizing voiceover")
    run_p.add_argument("--background-polling", action="store_true", help="Tell bot that supervisor is polling getUpdates")
    run_p.add_argument("--result-file", help="Write the outcome here as JSON (the daemon's worker contract)")
    run_p.add_argument("--regenerate-from", help="A run whose review asked to Regenerate: its reason guides this run")
    _reuse_options(run_p)
    run_p.add_argument("--paid-voice", action="store_true",
                       help="With --dry-run: voice with ElevenLabs anyway (spends credits); real runs always may")
    run_p.add_argument("--paid-motion", action="store_true",
                       help="With --dry-run: buy a motion clip anyway (spends media budget); real runs always may")
    run_p.add_argument("--schedule-posts", action="store_true",
                       help="Once approved, book the Reel into the next posting slot instead of posting at once")

    resume_p = sub.add_parser("resume", help="Continue a failed run, reusing its approved script and voiceover")
    resume_p.add_argument("run_id")
    resume_p.add_argument("--auto-approve", action="store_true")
    resume_p.add_argument("--dry-run", action="store_true")
    resume_p.add_argument("--require-live", action="store_true", help="A regeneration after this resume runs the live preflight")
    resume_p.add_argument("--background-polling", action="store_true", help="Tell bot that supervisor is polling getUpdates")
    resume_p.add_argument("--result-file", help="Write the outcome here as JSON (the daemon's worker contract)")
    resume_p.add_argument("--schedule-posts", action="store_true",
                          help="Once approved, book the Reel into the next posting slot instead of posting at once")

    sub.add_parser("bot", help="Run only the Telegram bot (image review, approvals, /status); no scheduled runs")
    facts_p = sub.add_parser("facts", help="Build verified fact sheets ahead of production")
    facts_p.add_argument("--topic-id")
    facts_p.add_argument("--next", type=int, default=3, help="Build for the next N queued topics without a fresh sheet")
    facts_p.add_argument("--force", action="store_true", help="Rebuild even if a fresh sheet exists")
    policy_p = sub.add_parser("policy", help="Check for new Meta policy changes now (web-searched, sourced)")
    policy_p.add_argument("--telegram", action="store_true", help="Also send the digest to Telegram")
    update_p = sub.add_parser("update", help="Check pinned packages for updates and known vulnerabilities")
    update_p.add_argument("--apply", action="store_true", help="Apply safe/security updates (test-gated, on a branch)")
    daemon_p = sub.add_parser("daemon", help="Run the daily scheduler")
    daemon_p.add_argument("--time", default=DAILY_RUN_TIME, help="Daily run time HH:MM")
    daemon_p.add_argument("--dry-run", action="store_true",
                          help="Everything runs (renders, reviews, approvals) but nothing is posted: approved runs are simulated")

    review_p = sub.add_parser("review", help="Inspect and decide pending approvals")
    review_sub = review_p.add_subparsers(dest="review_action")
    review_sub.add_parser("list")
    for action in ("approve", "reject", "regenerate"):
        decide_p = review_sub.add_parser(action)
        decide_p.add_argument("run_id")
        if action != "approve":
            from src.improve import REASONS as _reasons
            decide_p.add_argument("--reason", choices=sorted(_reasons), default="other",
                                  help="What was wrong (goes to the improvement ledger and, for regenerate, the new version)")
            decide_p.add_argument("--note", default="", help="Your words on what to change")
    send_p = review_sub.add_parser("send", help="Re-send the Telegram review card for a timed-out run and wait")
    send_p.add_argument("run_id")
    send_p.add_argument("--dry-run", action="store_true")
    pub_p = review_sub.add_parser("publish", help="Publish a rendered run that was never published")
    pub_p.add_argument("run_id")
    pub_p.add_argument("--dry-run", action="store_true")

    topic_p = sub.add_parser("topic", help="Inspect topic rotation")
    topic_sub = topic_p.add_subparsers(dest="topic_action")
    topic_sub.add_parser("next")
    topic_sub.add_parser("list")
    topic_sub.add_parser("requeue").add_argument("topic_id")
    topic_sub.add_parser("repair", help="Requeue topics wrongly counted as published by simulated runs")
    topic_sub.add_parser("propose", help="Research new topic ideas (they wait for 'topic add')")
    topic_sub.add_parser("add", help="Add a pending proposal to the topic bank").add_argument("slug")

    assets_p = sub.add_parser("assets", help="Review fetched scene images")
    assets_sub = assets_p.add_subparsers(dest="assets_action")
    assets_sub.add_parser("list")
    assets_sub.add_parser("approve", help="Mark a topic's fetched images as reviewed").add_argument("topic_id")
    review_p = assets_sub.add_parser("review", help="Vision review of photo pools: keep, drop and approve on merit")
    review_p.add_argument("topic_id", nargs="?", help="One topic (default: every pool not decided by the operator)")

    health_p = sub.add_parser("health", help="Pre-flight checks")
    health_p.add_argument("--live", action="store_true", help="Also require production credentials")
    test_p = sub.add_parser("test", help="End-to-end dry run on one topic (publishes nothing)")
    test_p.add_argument("--topic-id", help="Topic to test (default: the next topic production would make)")
    test_p.add_argument("--paid-voice", action="store_true", help="Voice this dry run with ElevenLabs (spends credits)")
    _reuse_options(test_p)
    test_p.add_argument("--paid-motion", action="store_true", help="Let this dry run buy a motion clip (spends media budget)")
    backup_p = sub.add_parser("backup", help="Sync completed runs to OneDrive / Cloud archive")
    backup_p.add_argument("--run-id", help="Specific run ID to backup")
    backup_p.add_argument("--all", action="store_true", help="Backup all finished runs in output/")

    args = parser.parse_args(argv)

    if args.command == "run":
        try:
            coordinator = PipelineCoordinator(schedule_posts=True) if args.schedule_posts else PipelineCoordinator()
            coordinator.paid_voice_in_dry_run = args.paid_voice
            coordinator.paid_motion_in_dry_run = args.paid_motion
            if getattr(args, "background_polling", False):
                coordinator.telegram_bot.background_polling = True
            refused = apply_reuse_options(coordinator, args)
            if refused:
                log.error(refused)
                _write_result(getattr(args, "result_file", None), {"status": "refused", "error": refused})
                return 1
            regenerations = 0
            previous = getattr(args, "regenerate_from", None)
            if previous:  # a Regenerate decided outside a running cycle (Resend, or after a restart)
                coordinator._operator_feedback = coordinator._await_reason(
                    previous, wait_seconds=reason_window(coordinator.db, previous, 120))
                # The same topic as the run it replaces, never the next one in the queue: a Kailasa
                # regeneration started on Greek Fire with Kailasa's feedback attached (2026-10-24).
                args.topic_id = args.topic_id or regeneration_topic(coordinator.db, previous)
                if not args.topic_id:
                    _write_result(getattr(args, "result_file", None), {"status": "refused", "error": f"Unknown run '{previous}'"})
                    return 1
                # The chain's count lives with the runs, so the limit holds across restarts (each resumed
                # regeneration counted as the first: 2026-10-23 audit).
                regenerations = int(coordinator.db.get_setting("regenerations:" + previous) or 0) + 1
                if regenerations > MAX_REGENERATIONS:
                    coordinator.telegram_bot.notify(
                        f"🔁 <b>Regeneration limit reached</b> ({MAX_REGENERATIONS}) for {html.escape(coordinator.db.run_label(previous))}. "
                        "Nothing new was made; Render the topic again from the queue for a fresh start.",
                        group="runs")
                    _write_result(getattr(args, "result_file", None), {"status": "regeneration_limit"})
                    return 0
            res = coordinator.run_cycle(args.topic_id, args.auto_approve, args.dry_run, require_live=getattr(args, "require_live", False),
                                        content_format=args.format, _regenerations=regenerations, run_id=getattr(args, "run_id", None))
        except QueueEmpty as e:
            _write_result(getattr(args, "result_file", None), {"status": "queue_empty", "detail": str(e)})
            return 0  # healthy: nothing new to make
        except Exception as e:
            _write_result(getattr(args, "result_file", None), {"status": "failed", "error": f"{type(e).__name__}: {e}"[:500]})
            return 1  # already logged and recorded on the run
        _write_result(getattr(args, "result_file", None), {k: res.get(k) for k in ("status", "run_id") if k in res})
        summary_keys = ["status", "run_id", "master_video", "dual_format"]
        out_dict = {k: res.get(k) for k in summary_keys if k in res}
        if "deep_dive_run" in res:
            out_dict["deep_dive_run"] = {
                k: res["deep_dive_run"].get(k) for k in ("run_id", "status", "master_video", "duration_seconds")
            }
        print(json.dumps(out_dict, indent=2))
        # A decided review is a normal end, not a failure: a Reject made bot mode log the worker as
        # "failed with code 1" (dev, 2026-10-23). Only a run that failed exits non-zero.
        return 0 if res["status"] in RUN_COMPLETED_STATUSES else 1

    if args.command == "resume":
        result_file = getattr(args, "result_file", None)
        try:
            coordinator = PipelineCoordinator(schedule_posts=True) if args.schedule_posts else PipelineCoordinator()
            if args.background_polling:
                coordinator.telegram_bot.background_polling = True
            res = coordinator.resume_run(args.run_id, args.auto_approve, args.dry_run,
                                         require_live=bool(getattr(args, "require_live", False)))
        except ValueError as e:
            print(f"Refusing: {e}")
            _write_result(result_file, {"status": "refused", "error": str(e)[:500]})
            return 1
        except Exception as e:
            _write_result(result_file, {"status": "failed", "error": f"{type(e).__name__}: {e}"[:500]})
            return 1  # already logged and recorded on the run
        _write_result(result_file, {k: res.get(k) for k in ("status", "run_id") if k in res})
        print(json.dumps({k: res.get(k) for k in ("status", "run_id", "master_video")}, indent=2))
        return 0 if res["status"] in RUN_COMPLETED_STATUSES else 1

    if args.command == "policy":
        import re
        from src import llm, policy_watch
        db = PipelineDatabase()
        llm.configure_usage(db.record_llm_usage, db.llm_spend)
        text = policy_watch.digest_text(policy_watch.check(db))
        print(html.unescape(re.sub(r"<[^>]+>", "", text)))
        if args.telegram:
            from src.telegram_bot import TelegramApprovalBot
            TelegramApprovalBot(db=db).notify(text, group="info")
        return 0

    if args.command == "update":
        import re
        from src import maintenance
        plan = maintenance.check()
        print(re.sub(r"<[^>]+>", "", maintenance.report_text(plan)))
        if args.apply:
            if not maintenance.apply_allowed():
                print(maintenance.PRODUCTION_REFUSAL)
                return 1
            try:
                print(maintenance.apply(plan))
            except Exception as e:
                print(f"Not applied (rolled back): {e}")
                return 1
        return 0

    if args.command == "facts":
        from src import fact_sheet
        db = PipelineDatabase()
        topics = [db.get_topic_by_id(args.topic_id)] if args.topic_id else prebuild_candidates(db, args.next)
        for t in topics:
            if t is None:
                print("Unknown topic"); return 1
            if fact_sheet.load(t["id"]) and not args.force:
                print(f"{t['id']}: fresh fact sheet exists"); continue
            sheet = fact_sheet.build(t)
            print(f"{t['id']}: {len(sheet['facts'])} facts ({sum(f['disputed'] for f in sheet['facts'])} disputed), "
                  f"{len(sheet['dropped'])} dropped")
        return 0

    if args.command == "bot":
        db = PipelineDatabase()
        coordinator = PipelineCoordinator(db=db, schedule_posts=POST_SCHEDULING)
        bot = coordinator.telegram_bot
        _report_interrupted(coordinator, bot)  # runs left "producing" by a process that ended
        record_run_mode(db, config.BOT_RUNS_DRY)  # the dashboard masthead says whether these runs post
        if not bot.is_configured():
            print("Telegram is not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / TELEGRAM_APPROVER_IDS; "
                  "the chat and approvers must identify people, not the bot).")
            return 1
        bot.register_commands()
        run_lock = threading.Lock()
        worker_proc = None

        review_waits: set = set()  # runs this process is waiting on (the sweep must not start a second)

        def follow_review(run_id: str, resend: bool) -> None:
            """Waits on a review and acts on its decision; a Regenerate starts its run in a worker."""
            def work():
                owner = run_id not in review_waits  # only the wait that added it takes it out (a resend's
                review_waits.add(run_id)            # early return removed the id the running wait still needed)
                try:
                    result = coordinator.request_approval(run_id, resend=resend)
                except Exception as e:
                    notifications.say(bot.notify, f"⚠️ Couldn't {'re-send' if resend else 'resume'} the review: {html.escape(str(e)[:300])}",
                        group="needs", key=f"review-lost:{run_id}", run=run_id,
                        actions=[{"text": "Resend for review", "callback_data": f"resend:{coordinator.db.run_key(run_id)}"}])
                    return
                finally:
                    if owner:
                        review_waits.discard(run_id)
                if result.get("decision") == "regenerate":
                    run = coordinator.db.get_run(run_id) or {}
                    started = launch_worker(run.get("content_format") or "reel",
                                            ["--topic-id", result["topic_id"], "--regenerate-from", run_id])
                    bot.notify(f"🔄 <b>Regenerating</b> {html.escape(coordinator.db.run_label(run_id))} with your feedback. {started}", group="runs")
            threading.Thread(target=work, name="review-wait", daemon=True).start()

        def resend_for_review(run_id: str) -> str:
            follow_review(run_id, resend=True)
            return f"📨 Sending {html.escape(coordinator.db.run_label(run_id))} for review again…"

        def trigger_run(content_format: str = "reel") -> str:
            return launch_worker(content_format)

        def launch_worker(content_format: str = "reel", extra: Optional[List[str]] = None, resume: Optional[str] = None) -> str:
            nonlocal worker_proc
            if run_lock.locked() or (worker_proc is not None and worker_proc.poll() is None):
                pid_info = f" (PID {worker_proc.pid})" if worker_proc else ""
                return notifications.Outcome(f"A production run is already in progress{pid_info}. Watch Telegram "
                                             "for progress updates.", done=False)

            def monitor_worker(proc, attempt, limits):
                deadline, hard_stop = limits  # seconds: the worker's deadline, and that plus its grace
                with run_lock:
                    log.info("Isolated production worker PID %d started.", proc.pid)
                    # Bounded: a worker that outruns its deadline and grace is stopped (src/worker_deadline.py)
                    stdout, stderr, stopped = worker_deadline.supervise(proc, hard_stop, coordinator.db, attempt)
                    rc = proc.returncode
                    if stopped:
                        worker_deadline.finalize_stopped_worker(coordinator.db, attempt, bot.notify, deadline / 60,
                                                                (hard_stop - deadline) / 60, bot.finish_progress_card)
                    if rc != 0:
                        log.error("Production worker PID %d failed with code %d:\n%s", proc.pid, rc, stdout or stderr)
                    else:
                        log.info("Production worker PID %d completed successfully.", proc.pid)
                    # As the daemon does: a worker that ended without finishing its run (a crash) leaves it
                    # "producing" with a live progress card until the next restart otherwise.
                    try:
                        cleared = reconcile_interrupted_runs(coordinator.db, coordinator.topic_engine)
                    except Exception as e:
                        log.warning("Could not check for an interrupted run: %s", e)
                        cleared = []
                    if cleared and config.TELEGRAM_CLEAN_CHAT:
                        say_interrupted(coordinator.db, bot, cleared, f"Its worker process ended (exit {rc}"
                                        + (f" / 0x{rc & 0xFFFFFFFF:08X}" if isinstance(rc, int) and rc else "") + ").")
                    elif cleared:
                        bot.notify(f"💥 <b>The run's worker process ended without finishing</b> (exit {rc}"
                                   + (f" / 0x{rc & 0xFFFFFFFF:08X}" if isinstance(rc, int) and rc else "")
                                   + "). Nothing was posted; the run was cleared and its topic is back in the queue.", group="needs")
                    # A batch (/produce N): the next episode starts once this worker has ended cleanly, which
                    # is after your decision on its review; a crash ends the batch.
                    try:
                        remaining = int(getattr(db, "get_setting", lambda key: None)(PRODUCE_KEY) or 0)
                    except (TypeError, ValueError, AttributeError):
                        remaining = 0
                    if remaining > 0 and rc == 0 and not cleared and batch_expired(db):
                        db.set_setting(PRODUCE_KEY, "0")
                    elif remaining > 0 and rc == 0 and not cleared:
                        db.set_setting(PRODUCE_KEY, str(remaining - 1))
                        threading.Timer(5, launch_worker).start()  # after this lock is released
                        bot.notify(f"🎬 Next episode of the batch is queued ({remaining - 1} more after it); its "
                                   "progress card shows when it starts.", group="runs")
                    elif remaining > 0:
                        db.set_setting(PRODUCE_KEY, "0")
                        bot.notify("⏹ Batch production stopped: the last episode did not finish cleanly.", group="runs")

            import uuid

            from src.config import BASE_DIR
            environment = config.worker_environment()  # an .env edit applies from the next run, not the next restart
            limits = config.run_deadline_limits(environment)  # the deadline this worker reads, and that plus its grace
            attempt = environment["RELIC_WORKER_ATTEMPT"] = uuid.uuid4().hex  # the runs it claims carry it
            # With posting slots on, the worker books the approved Reel like the daemon does.
            command = ["resume", resume, "--require-live"] if resume else ["run", "--require-live", "--format", content_format]
            worker_proc = subprocess.Popen(
                [sys.executable, "-u", "main.py", *command, "--background-polling"]
                + (["--dry-run"] if config.BOT_RUNS_DRY else [])  # nothing posts from a bot-started run by default
                + (["--schedule-posts"] if POST_SCHEDULING else []) + list(extra or []),
                cwd=str(BASE_DIR),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                **worker_deadline.launch_options(),
            )
            threading.Thread(target=monitor_worker, args=(worker_proc, attempt, limits), name="worker-monitor",
                             daemon=True).start()
            return notifications.Outcome(f"🎬 Production run launched in isolated worker (PID {worker_proc.pid}). Live "
                                         "progress updates will stream here.", done=True)

        def rerender_in_bot(run_id: str) -> str:
            return rerender_reply(coordinator, run_id, lambda extra: launch_worker(extra=extra) if extra
                                  else launch_worker(resume=run_id))

        PRODUCE_KEY = "produce_remaining"  # episodes still to start in a /produce batch

        def produce_batch(n: int) -> str:
            """/produce N (operator, 2026-10-24: a week of episodes in one go): the next N queued topics,
            one worker at a time; each comes to review, and the next starts once that worker has ended
            cleanly, i.e. after your decision."""
            db.set_setting(PRODUCE_KEY, str(max(0, n - 1)))
            db.set_setting(BATCH_STARTED_KEY, datetime.now(timezone.utc).isoformat())
            started = launch_worker()
            if "already in progress" in started:
                db.set_setting(PRODUCE_KEY, "0")
                return started
            return (f"🎬 Producing {n} episode(s) back to back: each comes to you for review, the next starts after "
                    f"your decision. {started}")

        bot.next_run_func = lambda: "on-demand (bot supervisor)"
        handler = make_command_handler(
            db, bot.next_run_func,
            trigger_run,
            send_photo=bot.send_photo, send_audio=bot.send_audio, send_message=lambda text, buttons=None: bot.notify(text, buttons=buttons, record_alert=False, group="studio"), delete_message=bot.delete_message, voice_quota=coordinator.audio_engine.voice_quota,
            propose_topics=_proposal_trigger(db, bot),
            run_actions={"pubok": coordinator.confirm_posted,
                         "pubno": coordinator.confirm_not_posted,
                         "resend": resend_for_review,
                         "cover": getattr(bot, "refresh_review_cover", None),
                         "edit": getattr(coordinator, "edit_copy", None),
                         "simpub": coordinator.simulate_publish,
                         "rerender": rerender_in_bot,
                         "promote": getattr(coordinator, "promote_run", None),
                         "postnow": getattr(coordinator, "post_now", None),
                         "cancelpost": getattr(coordinator, "cancel_post", None),
                         "movepost": getattr(coordinator, "move_post", None),
                         "retrypost": getattr(coordinator, "retry_post", None),
                         "retryreel": getattr(coordinator, "retry_reel", None),
                         "fbcaptions": getattr(coordinator, "add_facebook_captions", None),
                         "fbcover": getattr(coordinator, "set_facebook_cover", None),
                         "rebook": getattr(coordinator, "rebook_followers", None),
                         "newversion": lambda run_id: new_version(coordinator, run_id, rerender_in_bot,
                                                                  busy=lambda: db.get_active_progress() is not None),
                         "ytthumb": getattr(coordinator, "set_youtube_thumbnail", None),
                         "ytshort": getattr(coordinator, "post_youtube_short", None),
                         "carpreview": getattr(coordinator, "preview_carousel", None),
                         "carredraw": getattr(coordinator, "redraw_carousel", None),
                         "carspace": getattr(coordinator, "post_carousel_anyway", None),
                         "carexpire": getattr(coordinator, "expire_spaced_carousel", None)},
            produce_batch=produce_batch,
        )
        if POST_SCHEDULING:  # what this mode books, it also posts when due
            coordinator.only_promoted = bool(config.BOT_RUNS_DRY)  # bot-started runs are dry: only promoted ones post
            record_posting_mode(coordinator)
            start_post_ticker(coordinator, bot)
        bot.background_polling = True  # set before any review wait starts (see below)
        resume_reviews(coordinator, follow_review, review_waits)

        def sweep_reviews():
            """Every 2 minutes: decided reviews nothing is acting on (a command-line decision, say)."""
            while True:
                time.sleep(120)
                try:
                    if not (worker_proc is not None and worker_proc.poll() is None):  # a worker may be the waiter
                        resume_reviews(coordinator, follow_review, review_waits, decided_only=True)
                except Exception as e:
                    log.debug("Review sweep failed: %s", e)
        threading.Thread(target=sweep_reviews, name="review-sweep", daemon=True).start()
        from src.control import startup_dashboard_card
        card_text, card_buttons = startup_dashboard_card(db, mode="bot", assets_dir=config.ASSETS_DIR)
        bot.warm_up()
        bot.show_dashboard(card_text, card_buttons)
        # This loop owns getUpdates. A review wait this process runs itself (Resend) must read decisions
        # from the database: polling as well made Telegram cut one poller off with 409 Conflict every few
        # seconds, which can drop taps (dev, 2026-10-23). The daemon sets the same.
        bot.background_polling = True
        log.info("Studio Control Center: listening for Telegram commands. Ctrl+C to stop.")
        try:
            poll_loop(bot, handler)
        except KeyboardInterrupt:
            if config.TELEGRAM_CLEAN_CHAT:
                # UI-01f (design §7.3): the dashboard's footer and the service-state item; the panel is drawn once
                # more and the renderer lease given up (close_layout's job), so the next start renders at once
                bot.service_stopped()
            else:
                bot.notify("🔴 <b>Studio Control Center Offline</b> • Service stopped.", record_alert=False,
                           group="info", key="service-state")
        return 0

    if args.command == "daemon":
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger

        from src.config import HEARTBEAT_TIME

        hour, minute = (int(x) for x in args.time.split(":"))
        hb_hour, hb_minute = (int(x) for x in HEARTBEAT_TIME.split(":"))
        coordinator = PipelineCoordinator(schedule_posts=True)
        bot = coordinator.telegram_bot
        _report_interrupted(coordinator, bot)  # nothing runs in this process yet: any "producing" run is orphaned
        record_run_mode(coordinator.db, bool(args.dry_run))  # the masthead: 📡 LIVE posting or 🧪 dry run
        release_note = None  # after an installed release: the notice that opens the one dashboard panel
        try:  # leases left by the previous process; the schedule the release deployer keeps clear of
            clear_busy_leases(coordinator.db)
            coordinator.db.set_setting("daemon_schedule", json.dumps({"time": args.time, "timezone": TIMEZONE}))
            release_note = announce_release(coordinator.db, bot, startup=True)
        except Exception as e:
            log.warning("Could not record the daemon's release state: %s", e)
        try:  # .env kept up to date with the release's template, nothing changed, no secret shown (src/env_sync.py)
            from src.env_sync import sync_on_start
            settings_note = sync_on_start()
            if settings_note and settings_note != coordinator.db.get_setting("env_sync_reported"):
                bot.notify(settings_note, record_alert=False, group="info")
                coordinator.db.set_setting("env_sync_reported", settings_note)
        except Exception as e:
            log.warning("Settings check did not run: %s", e)
        bot.register_commands()
        run_lock = threading.Lock()  # one production run at a time (daily job or /run)

        PRODUCE_KEY = "produce_remaining"  # episodes still to start in a /produce batch

        def continue_batch(status: Optional[str], resumed: bool = False, manual: bool = True) -> None:
            """/produce N in the daemon (operator, 2026-10-25: "bot says I don't know /produce"; production runs the
            daemon, where the batch was never wired): the next episode starts once this run's worker has ended
            cleanly, which is after the operator's decision on its review; a crash or an empty queue ends the batch.
            Only the batch's own new episodes move it on: rejecting a 2-day-old Richat card, followed by a resumed
            review wait, started a forgotten batch's last episode (production, 2026-10-28)."""
            try:
                remaining = int(coordinator.db.get_setting(PRODUCE_KEY) or 0)
            except (TypeError, ValueError, AttributeError):
                remaining = 0
            if remaining <= 0 or resumed or not manual:  # the daily run is not one of the batch's episodes
                return
            if batch_expired(coordinator.db):
                coordinator.db.set_setting(PRODUCE_KEY, "0")
                log.info("A /produce batch older than %d h was dropped instead of continued.", BATCH_MAX_HOURS)
                bot.notify(f"⏹ <b>Batch stopped</b>: it was started more than {BATCH_MAX_HOURS} h ago (or before this "
                           f"release), so its {remaining} remaining episode(s) were not started. Send /produce again to "
                           "make more.", group="runs")
                return
            if status in ("published", "simulated", "rejected", "scheduled"):
                coordinator.db.set_setting(PRODUCE_KEY, str(remaining - 1))
                scheduler.add_job(production_run, kwargs={"manual": True})
                # Queued, which is all that is known here: the run itself says when it starts (a release install
                # restarted the daemon a minute after this said "starting", and nothing started; 2026-10-28)
                bot.notify(f"🎬 Next episode of the batch is queued ({remaining - 1} more after it); its progress "
                           "card shows when it starts.", group="runs")
            else:
                coordinator.db.set_setting(PRODUCE_KEY, "0")
                bot.notify("⏹ Batch production stopped: the last episode did not finish cleanly.", group="runs")

        def production_run(manual: bool = False, content_format: str = "reel", extra: Optional[List[str]] = None,
                           resume: Optional[str] = None):
            if not manual and is_paused(coordinator.db):
                log.info("Scheduled run skipped: paused.")
                bot.notify("⏸ Scheduled run skipped (paused). Send /resume to re-enable.",
                           transient=True, ttl_minutes=23 * 60 + 50, group="runs", key="paused-skip")
                return
            if not run_lock.acquire(blocking=False):
                log.warning("A run is already in progress; skipping.")
                return
            status = None
            try:
                # Each run in its own process: a crash costs the run, never the daemon (its bot and schedule).
                with busy_lease(coordinator.db, "the production run"):
                    result = run_in_worker(content_format, dry_run=args.dry_run, schedule_posts=POST_SCHEDULING, extra=extra,
                                           resume=resume)  # supervised against this process's database
                status = result.get("status")
                if status in ("published", "simulated", "rejected", "scheduled"):
                    ping_healthcheck()
                elif status == "queue_empty":
                    ping_healthcheck()  # the system is healthy; it just has nothing new to make
                    if "timed-out review" in str(result.get("detail", "")):  # held, not exhausted
                        bot.notify("⌛ <b>Today's run was skipped</b>: every topic left waits for a timed-out review. "
                                   "Resend or Reject it.", [{"text": "🎬 Reviews", "callback_data": "creviews:"}], group="needs")
                    else:
                        bot.notify("📭 <b>Topic queue is empty</b>: every topic has been published, so today's run was skipped "
                                   "(repeating topics risks Meta's unoriginal-content rules).",
                                   [{"text": "💡 Propose new topics", "callback_data": "propose:"}], group="needs")
                elif status == "refused":  # a re-render the run's state no longer allows
                    run_ref = coordinator.db.resolve_run(resume) if resume else None
                    notifications.say(bot.notify, f"🔁 <b>Re-render not started</b>: {html.escape(str(result.get('error', '')))}", group="needs",
                        key=f"fail:{run_ref}" if run_ref else None, run=run_ref,
                        actions=[{"text": "Details", "callback_data": f"chist:{coordinator.db.run_key(run_ref)}"}] if run_ref else None)
                elif status == "timed_out" and result.get("stopped"):  # the worker did not stop itself at its deadline
                    deadline, hard_stop = config.run_deadline_limits()
                    worker_deadline.finalize_stopped_worker(coordinator.db, result["attempt"], bot.notify, deadline / 60,
                                                            (hard_stop - deadline) / 60, bot.finish_progress_card)
                elif status == "crashed":
                    code = result.get("exit_code")
                    log.error("Run worker crashed (exit %s); the daemon carries on.\n%s", code, result.get("stderr_tail", ""))
                    cleared = reconcile_interrupted_runs(coordinator.db, coordinator.topic_engine)
                    if cleared and config.TELEGRAM_CLEAN_CHAT:   # the run's own item (one item for one failure)
                        say_interrupted(coordinator.db, bot, cleared, f"Its worker process crashed (exit {html.escape(str(code))}"
                                        + (f" / 0x{int(code) & 0xFFFFFFFF:08X}" if isinstance(code, int) else "")
                                        + "); the daemon, its bot and the schedule are unaffected.")
                    else:
                        notifications.say(bot.notify, f"💥 <b>The run's worker process crashed</b> (exit {html.escape(str(code))}"
                            + (f" / 0x{int(code) & 0xFFFFFFFF:08X}" if isinstance(code, int) else "")
                            + "). Nothing was posted; the daemon, its bot and the schedule are unaffected."
                            + (" The run was cleared and its topic is back in the queue." if cleared else ""), group="needs",
                            key="worker-crash", actions=[{"text": "📊 Status", "callback_data": "cstat:"}])
            except BaseException as e:  # keep the daemon alive; the run is already recorded and alerted
                log.exception("Run failed: %s", e)
            finally:
                run_lock.release()
                try:
                    continue_batch(status, resumed=resume is not None, manual=manual)
                except Exception as e:
                    log.warning("Batch continuation failed: %s", e)

        def produce_batch(n: int) -> str:
            coordinator.db.set_setting(PRODUCE_KEY, str(max(0, n - 1)))
            coordinator.db.set_setting(BATCH_STARTED_KEY, datetime.now(timezone.utc).isoformat())
            started = trigger_run()
            if "already in progress" in started:
                coordinator.db.set_setting(PRODUCE_KEY, "0")
                return started
            return (f"🎬 Producing {n} episode(s) back to back: each comes to you for review, the next starts after "
                    f"your decision. {started}")

        scheduler = BlockingScheduler(timezone=TIMEZONE)
        # The daily run asks for every format; run_cycle narrows it to VIDEO_FORMATS (default: the Reel only).
        daily = scheduler.add_job(production_run, CronTrigger(hour=hour, minute=minute, timezone=TIMEZONE),
                                  kwargs={"content_format": "dual"},
                                  max_instances=1, coalesce=True, misfire_grace_time=3600)

        def next_run() -> Optional[str]:
            when = getattr(daily, "next_run_time", None)  # set once the scheduler is running
            return when.strftime("%a %d %b %H:%M %Z") if when else None

        def heartbeat():
            # On the panel, not a message of its own (operator, 2026-10-25: it repeated the full status): the
            # dashboard is redrawn with a "💓 heartbeat" line. Silent, as the message was; the outside
            # health check still hears from the daemon.
            from src.control import record_heartbeat
            record_heartbeat(coordinator.db)
            backup_check()
            bot.show_dashboard()
            ping_healthcheck()

        def backup_check():
            # The backup worker's receipt, read hourly: a stale snapshot, lapsed drill or missing hourly copy is a
            # Needs-you notice once a day per set of alerts (QF-72). Production only; dev has no backup worker.
            from src.backup_health import monitored, notify_stale
            try:
                if monitored():
                    notify_stale(coordinator.db, bot)
            except Exception as e:  # noqa: BLE001 - never in the way of the heartbeat it runs inside
                log.warning("Backup freshness check failed: %s", e)

        scheduler.add_job(heartbeat, CronTrigger(hour=hb_hour, minute=hb_minute, timezone=TIMEZONE),
                          max_instances=1, coalesce=True, misfire_grace_time=3600)
        scheduler.add_job(backup_check, "interval", hours=1, max_instances=1, coalesce=True)

        def post_due():
            if is_paused(coordinator.db):
                return  # held; slots missed while paused move to the next free slot on resume
            try:
                coordinator.post_due()
                say_posting_error(coordinator.db, bot, None)
            except Exception as e:
                log.exception("Posting scheduled items failed: %s", e)
                say_posting_error(coordinator.db, bot, e)

        def report_release():  # a release refused before the stop window causes no restart to announce it
            try:
                announce_release(coordinator.db, bot)
            except Exception as e:
                log.debug("Release receipt check failed: %s", e)

        scheduler.add_job(report_release, "interval", minutes=5, max_instances=1, coalesce=True)

        def reconcile_runs():
            # A run whose process ended mid-way (killed, crashed) is said and cleared now, not at the next daemon
            # start: a stopped development test sat "producing" behind a card that said "no longer current. Run
            # status: producing" (operator, 2026-10-28). A run whose process is alive or can't be checked is untouched.
            if run_lock.locked():
                return  # this daemon's own run: its worker is checked by its own supervision
            _report_interrupted(coordinator, bot)

        scheduler.add_job(reconcile_runs, "interval", minutes=10, max_instances=1, coalesce=True)

        def pick_up_credentials():
            # A key the operator adds to .env is used without a restart where the code reads it at each use
            # (operator, 2026-10-28: production's restart needs an administrator). Said once, by name only.
            from src.env_sync import pick_up_credentials as take
            taken = take()
            if taken:
                log.info("Credential(s) taken in from .env without a restart: %s", ", ".join(taken))
                bot.notify("🔑 Now in use, no restart needed: " + ", ".join(f"<code>{k}</code>" for k in taken)
                           + ". The next photo search uses it.", record_alert=False, group="info")

        scheduler.add_job(pick_up_credentials, "interval", minutes=2, max_instances=1, coalesce=True)

        def watch_deployer():  # a release about to install is announced before the bot goes quiet for it
            try:
                from src.control import deployer_notice
                notice = deployer_notice(coordinator.db)
                if notice:
                    bot.notify(notice, silent=True, record_alert=False, group="info", key="release")
            except Exception as e:
                log.debug("Deployer status check failed: %s", e)

        scheduler.add_job(watch_deployer, "interval", seconds=20, max_instances=1, coalesce=True)

        if POST_SCHEDULING:  # approved Reels are booked into slots; this publishes them when due
            coordinator.only_promoted = bool(args.dry_run)  # a dry-run daemon posts only runs promoted with "Post for real"
            record_posting_mode(coordinator)
            scheduler.add_job(post_due, "interval", minutes=1, max_instances=1, coalesce=True)

        from src import maintenance

        def weekly_update_check():
            try:
                plan = maintenance.check()
            except Exception as e:
                bot.notify(f"🧰 Weekly update check failed: {html.escape(str(e)[:300])}", group="info")
                return
            say_update_report(bot, plan)   # an item of the notifications panel, never a message of its own

        scheduler.add_job(weekly_update_check, CronTrigger(day_of_week="mon", hour=hb_hour, minute=hb_minute + 5 if hb_minute < 55 else 0,
                                                           timezone=TIMEZONE), max_instances=1, coalesce=True, misfire_grace_time=3600)

        from src import policy_watch

        def weekly_policy_watch():
            try:
                bot.notify(policy_watch.digest_text(policy_watch.check(coordinator.db)), transient=True, ttl_minutes=10070, group="info",
                           key="policy:weekly")
            except Exception as e:
                bot.notify(f"📜 Weekly policy watch failed: {html.escape(str(e)[:300])}", group="info", key="policy:weekly")

        scheduler.add_job(weekly_policy_watch, CronTrigger(day_of_week="wed", hour=hb_hour, minute=hb_minute,
                                                           timezone=TIMEZONE), max_instances=1, coalesce=True,
                          misfire_grace_time=3600)

        review_waits: set = set()  # runs this process is waiting on (the sweep must not start a second)

        def follow_review(run_id: str, resend: bool) -> None:
            """Waits on a review in the background and acts on its decision; a Regenerate becomes a
            production run of the same topic, in its worker, guided by the reason given."""
            def work():
                owner = run_id not in review_waits  # only the wait that added it takes it out (a resend's
                review_waits.add(run_id)            # early return removed the id the running wait still needed)
                try:
                    result = coordinator.request_approval(run_id, dry_run=args.dry_run, resend=resend)
                except Exception as e:
                    notifications.say(bot.notify, f"⚠️ Couldn't {'re-send' if resend else 'resume'} the review: {html.escape(str(e)[:300])}",
                        group="needs", key=f"review-lost:{run_id}", run=run_id,
                        actions=[{"text": "Resend for review", "callback_data": f"resend:{coordinator.db.run_key(run_id)}"}])
                    return
                finally:
                    if owner:
                        review_waits.discard(run_id)
                if result.get("decision") == "regenerate":
                    run = coordinator.db.get_run(run_id) or {}
                    scheduler.add_job(production_run, kwargs={
                        "manual": True, "content_format": run.get("content_format") or "reel",
                        "extra": ["--topic-id", result["topic_id"], "--regenerate-from", run_id]})
                    bot.notify(f"🔄 <b>Regenerating</b> {html.escape(coordinator.db.run_label(run_id))} with your feedback; "
                               "its progress card follows.", group="runs")
            threading.Thread(target=work, name="review-wait", daemon=True).start()

        def resend_for_review(run_id: str) -> str:
            """Re-sends a Reel's review card (in the background; the wait can be long)."""
            follow_review(run_id, resend=True)
            return f"📨 Sending {html.escape(coordinator.db.run_label(run_id))} for review again…"

        propose_topics = _proposal_trigger(coordinator.db, bot)
        def prebuild_fact_sheets():
            """Nightly: verified fact sheets for the next queued topics, so research (and any trouble with
            it) happens well before the daily run, not at 9:00."""
            with busy_lease(coordinator.db, "the nightly fact-sheet build"):
                _prebuild_fact_sheets()

        def _prebuild_fact_sheets():
            from src import fact_sheet
            from src.asset_pipeline import ensure_pool
            try:  # photo pools for the next topics too, so a run never waits on review
                not_ready = [t for t in coordinator.db.ranked_topics()[:3] if not ensure_pool(t)]
                if not_ready:  # skipped topics should never go unnoticed
                    from src.asset_pipeline import not_ready_reason  # the live reason, by the video's own count
                    lines = [f"• {html.escape(t['title'])}: {html.escape(not_ready_reason(t['id']))}" for t in not_ready]
                    bot.notify("🖼 <b>Photo sets not ready</b>\n\nThese upcoming topics will be skipped until "
                               "they have approved photos:\n" + "\n".join(lines), silent=True, group="needs",
                               key="photos-not-ready", actions=[{"text": "🖼 Pictures", "callback_data": "cimages:"}])
            except Exception as e:
                log.warning("Nightly photo pool preparation failed: %s", e)
            for t in prebuild_candidates(coordinator.db, 3):
                try:
                    if not fact_sheet.load(t["id"]):
                        fact_sheet.build(t)
                except Exception as e:
                    log.warning("Fact sheet pre-build failed for %s: %s", t["id"], e)
                    bot.notify(f"🧾 Couldn't build the fact sheet for {html.escape(t['title'])}: {html.escape(str(e)[:200])}", group="needs",
                               key=f"fact-sheet:{t['id']}", actions=[{"text": "Details", "callback_data": "cstat:"}])

        scheduler.add_job(prebuild_fact_sheets, CronTrigger(hour=3, minute=0, timezone=TIMEZONE),
                          max_instances=1, coalesce=True, misfire_grace_time=6 * 3600)
        # Nightly (QF-73): research proposes ideas for your Add while the queue is short, at most
        # TOPIC_RESEARCH_RUNS_PER_WEEK times a week (1: the weekly run it replaces, so spend does not rise).
        from src.topic_policy import nightly_proposals
        nightly_research = _proposal_trigger(coordinator.db, bot, config.TOPIC_NIGHTLY_PROPOSALS)
        proposal_hour, proposal_minute = (int(x) for x in config.TOPIC_PROPOSAL_TIME.split(":"))
        scheduler.add_job(lambda: nightly_proposals(coordinator.db, nightly_research.run_now),
                          CronTrigger(hour=proposal_hour, minute=proposal_minute, timezone=TIMEZONE),
                          max_instances=1, coalesce=True, misfire_grace_time=3600)

        def improvement_cycle():
            """Nightly: recurring findings become proposals; candidate writing rules are tested on the
            eval (free quota only, a few drafts a night, never during a production run); a rule that
            wins, and any code-level proposal, is put to you as a card. Nothing is adopted without you."""
            with busy_lease(coordinator.db, "the improvement cycle"):
                _improvement_cycle()

        def _improvement_cycle():
            from src import accounts, improve
            try:
                pruned = coordinator.db.prune_history()
                if any(pruned.values()):
                    log.info("Pruned old rows: %s", pruned)
            except Exception as e:
                log.warning("History pruning failed: %s", e)
            try:  # the publishing accounts really work (not just "a key is set")
                problems = accounts.check(coordinator.db, force=True).get("problems") or []
                if problems:
                    bot.notify("⚠️ <b>Publishing accounts</b>\n" + "\n".join(f"• {html.escape(p)}" for p in problems),
                               silent=True, group="needs", key="accounts",
                               actions=[{"text": "🔗 Accounts", "callback_data": "caccounts:"}])
            except Exception as e:
                log.warning("Account check failed: %s", e)
            try:
                for p in improve.make_proposals(coordinator.db):
                    if p["status"] == "ready":
                        proposal_notice(bot, coordinator.db, p)
                if improve.proposals(coordinator.db, "testing") and run_lock.acquire(blocking=False):
                    try:
                        from scripts.eval_prompts import run_candidates
                        rows = run_candidates(coordinator.db)
                    finally:
                        run_lock.release()
                    for p in improve.update_gates(coordinator.db, rows):
                        if p["status"] == "ready":
                            proposal_notice(bot, coordinator.db, p)
            except Exception as e:
                # Said in Telegram: the cycle failed every night on production for days as a log line only
                # (2026-10-24). A broken improvement loop is itself something that needs you.
                log.warning("Improvement cycle failed: %s", e)
                from src.control import short_reason
                bot.notify("🧭 <b>Improvement cycle failed</b> tonight, so no findings were turned into proposals.\n"
                           f"<blockquote expandable>{html.escape(short_reason(str(e), 600))}</blockquote>", silent=True, group="info")

        def weekly_digest():
            from src import improve
            try:
                # Kept for the dashboard's 📰 Weekly digest panel, and a nudge that leaves after a minute
                # (operator, 2026-10-25): no message that stays in the chat for a week.
                from src.control import record_weekly_digest
                record_weekly_digest(coordinator.db, improve.digest_text(coordinator.db))
                bot.show_dashboard()
                from src import notice_conditions
                notice_conditions.announce_digest(coordinator.db, bot)
            except Exception as e:
                log.warning("Weekly digest failed: %s", e)

        def collect_audience():
            from src import audience
            try:
                n = audience.collect(coordinator.db)
                if n:
                    log.info("Audience metrics: %d snapshot(s) stored.", n)
                    log.info("Audience sources: %s", " | ".join(audience.sources_health(coordinator.db)["lines"]))
                # Weak results go to the audit ledger, where a recurring one becomes a proposal.
                filed = audience.file_findings(coordinator.db)
                if filed:
                    log.info("Audience: %d finding(s) filed to the improvement ledger.", filed)
            except Exception as e:
                log.warning("Audience metrics collection failed: %s", e)

        scheduler.add_job(improvement_cycle, CronTrigger(hour=4, minute=0, timezone=TIMEZONE),
                          max_instances=1, coalesce=True, misfire_grace_time=6 * 3600)
        scheduler.add_job(weekly_digest, CronTrigger(day_of_week="sun", hour=hb_hour, minute=hb_minute, timezone=TIMEZONE),
                          max_instances=1, coalesce=True, misfire_grace_time=6 * 3600)
        # Hourly: a Story's insights live 24 h and are read from 18 h, so one missed 6-hourly pass lost them (2026-10-28);
        # a pass with nothing due is one query
        scheduler.add_job(collect_audience, CronTrigger(minute=20, timezone=TIMEZONE),
                          max_instances=1, coalesce=True, misfire_grace_time=3 * 3600)

        def check_accounts_now():  # so status and the dashboard show real account state from the start
            try:
                from src import accounts
                accounts.check(coordinator.db, force=True)
            except Exception as e:
                log.warning("Account check failed: %s", e)
        threading.Thread(target=check_accounts_now, name="account-check", daemon=True).start()

        def apply_updates() -> str:
            def work():
                with run_lock, busy_lease(coordinator.db, "package updates"):  # never under a running production cycle
                    try:
                        bot.notify(maintenance.apply(maintenance.check()), group="info")
                    except Exception as e:
                        bot.notify(f"❌ Updates not applied (rolled back): {html.escape(str(e)[:500])}", group="needs",
                                   key="updates", actions=[{"text": "Details", "callback_data": "cstat:"}])

            return apply_updates_reply(run_lock.locked(), threading.Thread(target=work, name="apply-updates",
                                                                           daemon=True).start)

        def trigger_run(content_format: str = "reel", extra: Optional[List[str]] = None) -> str:
            if run_lock.locked():
                return "A run is already in progress."
            scheduler.add_job(production_run, kwargs={"manual": True, "content_format": content_format, "extra": extra})
            if extra:  # /render fresh STEP, /render reuse N
                return ("🎬 Starting a run now (" + html.escape(" ".join(extra)) + "). You'll get the review card when "
                        "it's rendered; nothing posts without your approval.")
            return "🎬 Starting a run now. You'll get the review card when it's rendered; nothing posts without your approval."

        def rerender_run(run_id: str) -> str:
            def launch(extra: list[str]):
                if run_lock.locked():
                    return notifications.Outcome("🔁 A run is already in progress; Re-render this one when it has "
                                                 "finished.", done=False)
                if extra:  # rejected: a regeneration with the recorded reason, reusing what was paid for
                    scheduler.add_job(production_run, kwargs={"manual": True, "extra": extra})
                else:
                    scheduler.add_job(production_run, kwargs={"manual": True, "resume": run_id})
                return None
            return rerender_reply(coordinator, run_id, launch)

        bot.next_run_func = next_run
        handler = make_command_handler(coordinator.db, next_run, trigger_run, send_photo=bot.send_photo, send_audio=bot.send_audio, send_message=lambda text, buttons=None: bot.notify(text, buttons=buttons, record_alert=False, group="studio"), delete_message=bot.delete_message,
                                       voice_quota=coordinator.audio_engine.voice_quota,
                                       apply_updates=apply_updates, propose_topics=propose_topics,
                                       run_actions={"pubok": coordinator.confirm_posted,
                                                    "pubno": coordinator.confirm_not_posted,
                                                    "resend": resend_for_review,
                                                    "cover": getattr(bot, "refresh_review_cover", None),
                                                    "edit": getattr(coordinator, "edit_copy", None),
                                                    "simpub": coordinator.simulate_publish,
                                                    "rerender": rerender_run,
                                                    "promote": getattr(coordinator, "promote_run", None),
                                                    "postnow": getattr(coordinator, "post_now", None),
                                                    "cancelpost": getattr(coordinator, "cancel_post", None),
                                                    "movepost": getattr(coordinator, "move_post", None),
                                                    "retrypost": getattr(coordinator, "retry_post", None),
                                                    "retryreel": getattr(coordinator, "retry_reel", None),
                         "fbcaptions": getattr(coordinator, "add_facebook_captions", None),
                         "fbcover": getattr(coordinator, "set_facebook_cover", None),
                         "rebook": getattr(coordinator, "rebook_followers", None),
                         "newversion": lambda run_id: new_version(coordinator, run_id, rerender_run,
                                                                  busy=lambda: run_lock.locked()
                                                                  or coordinator.db.get_active_progress() is not None),
                         "ytthumb": getattr(coordinator, "set_youtube_thumbnail", None),
                         "ytshort": getattr(coordinator, "post_youtube_short", None),
                         "carpreview": getattr(coordinator, "preview_carousel", None),
                         "carredraw": getattr(coordinator, "redraw_carousel", None),
                         "carspace": getattr(coordinator, "post_carousel_anyway", None),
                         "carexpire": getattr(coordinator, "expire_spaced_carousel", None)},
                                       produce_batch=produce_batch)

        def poll_telegram():
            poll_loop(bot, handler)

        if bot.is_configured():
            bot.warm_up()
            bot.background_polling = True  # this thread owns getUpdates; approval waits read the database
            threading.Thread(target=poll_telegram, name="telegram-poller", daemon=True).start()
        resume_reviews(coordinator, follow_review, review_waits)  # cards still in the chat keep working after a restart

        def sweep_reviews():  # decided reviews nothing is acting on (a command-line decision, say)
            if not run_lock.locked():  # a production worker may be the one waiting
                resume_reviews(coordinator, follow_review, review_waits, decided_only=True)

        scheduler.add_job(sweep_reviews, "interval", minutes=2, max_instances=1, coalesce=True)

        log.info("Daemon scheduled daily at %s (%s); heartbeat at %s. Ctrl+C to stop.", args.time, TIMEZONE, HEARTBEAT_TIME)
        try:
            from src.control import startup_dashboard_card
            card_text, card_buttons = startup_dashboard_card(coordinator.db, mode="daemon", time_str=args.time, timezone_str=TIMEZONE, assets_dir=config.ASSETS_DIR)
            if args.dry_run:
                card_text = card_text.replace("\n\n", "\n\n🧪 <b>Validation mode</b>: runs, reviews and approvals work, but nothing is posted.\n\n", 1)
            if release_note:  # the chat was just cleared: the release leads the panel (one panel, not two)
                card_text = card_text.replace("\n\n", f"\n\n{release_note}\n\n", 1)
            scheduler.add_job(lambda: bot.show_dashboard(card_text, card_buttons))
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            log.info("Daemon stopped.")
            if config.TELEGRAM_CLEAN_CHAT:
                bot.service_stopped()   # UI-01f: footer, service-state item, the lease given up (design §7.3)
            else:
                bot.notify("🔴 <b>Production Daemon Offline</b> • Scheduled runs stopped.", record_alert=False,
                           group="info", key="service-state")
        return 0

    if args.command == "review":
        db = PipelineDatabase()
        if args.review_action == "list":
            pending = db.list_pending_approvals()
            print(f"Pending approvals: {len(pending)}")
            for item in pending:
                print(f"  {item['run_id']} | {item['script_payload'].get('title', item['topic_id'])} | {item['created_at']}")
        elif args.review_action in ("approve", "reject", "regenerate"):
            decision = {"approve": "approved", "reject": "rejected", "regenerate": "regenerate"}[args.review_action]
            appr = db.get_approval(args.run_id) or {}
            blocked = gate_problems(db, args.run_id) if decision == "approved" and appr.get("decision") == "pending" else []
            if blocked:
                # As on the card, which offers no Approve here: the review stays open for Edit, Regenerate or
                # Reject (dev, 2026-10-27: approved seven times, refused at release each time).
                print(f"Refusing: {args.run_id} is blocked at the release gate, so it is not approved:\n  - "
                      + "\n  - ".join(blocked) + "\nThe review stays open: edit, regenerate or reject it.")
                return 1
            if db.update_approval_decision(args.run_id, decision, decided_by="cli"):
                if decision != "approved":
                    from src import improve
                    improve.record_reason(db, args.run_id, args.reason, decision, args.note)
                try:  # the card in Telegram must not stay tappable after a CLI decision
                    from src.telegram_bot import TelegramApprovalBot
                    TelegramApprovalBot(db=db).dismiss_approval_buttons(args.run_id)
                except Exception as e:
                    log.debug("Could not update the Telegram card: %s", e)
                print(f"{args.run_id}: {decision}. The waiting run picks this up within seconds.")
            else:
                appr = db.get_approval(args.run_id)
                print(f"{args.run_id}: no pending approval (current: {appr and appr['decision']}). "
                      f"To publish a finished run, use `review publish {args.run_id}`.")
                return 1
        elif args.review_action == "send":
            try:
                res = PipelineCoordinator(db).request_approval(args.run_id, dry_run=args.dry_run)
            except ValueError as e:
                print(f"Refusing: {e}")
                return 1
            print(json.dumps(res, indent=2, default=str))
            return 0 if res["status"] in ("published", "simulated") else 1
        elif args.review_action == "publish":
            run = db.get_run(args.run_id)
            if not run:
                print(f"Unknown run {args.run_id}")
                return 1
            if run["status"] in ("published", "publish_unknown"):
                print(f"Refusing: run status is '{run['status']}'.")
                return 1
            if run["status"] not in ("pending_approval", "timeout", "approved", "simulated"):
                print(f"Refusing: run status '{run['status']}' is not a finished, unpublished render.")
                return 1
            blocked = gate_problems(db, args.run_id, dry_run=bool(args.dry_run) or None)
            if blocked:
                print(f"Refusing: {args.run_id} is blocked at the release gate, so it is not approved:\n  - "
                      + "\n  - ".join(blocked))
                return 1
            db.record_approval(args.run_id, run["topic_id"])
            db.update_approval_decision(args.run_id, "approved", decided_by="cli-publish")
            try:
                res = PipelineCoordinator(db).publish_run(args.run_id, dry_run=args.dry_run)
            except ValueError as e:
                print(f"Refusing: {e}")
                return 1
            print(json.dumps(res, indent=2, default=str))
            return 0 if res["status"] in ("published", "simulated") else 1
        else:
            review_p.print_help()
        return 0

    if args.command == "topic":
        db = PipelineDatabase()
        if args.topic_action == "next":
            t = db.get_next_topic()
            print(f"Next topic: {t['title']} ({t['id']})\nHook: {t['hook']}\n"
                  f"Location: {t['location'].get('name')}, {t['location'].get('country')}")
        elif args.topic_action == "list":
            topics = db.list_all_topics()
            print(f"Topic bank: {len(topics)} topics, {sum(1 for t in topics if t['times_published'])} published")
            for t in topics:
                print(f"  [{t['status']:<15}] x{t['times_published']} {t['id']}: {t['title']}")
        elif args.topic_action == "requeue":
            t = db.get_topic_by_id(args.topic_id)
            if t and t["times_published"]:
                answer = input(f"'{t['title']}' has already been published {t['times_published']}x. Repeating a topic "
                               "risks Meta's unoriginal-content rules. Requeue anyway? [y/N] ")
                if answer.strip().lower() != "y":
                    print("Not requeued.")
                    return 1
            db.requeue_topic(args.topic_id)
            print(f"Requeued {args.topic_id}.")
        elif args.topic_action == "propose":
            from src import llm, topic_proposer
            llm.configure_usage(db.record_llm_usage, db.llm_spend)
            for p in topic_proposer.propose(db):
                print(f"\n[{p.slug}] {p.title}\n  {p.hook}\n  sources: {', '.join(s.url for s in p.sources[:2])}")
            print("\nAdd one with: python main.py topic add <slug>")
        elif args.topic_action == "add":
            from src import topic_proposer
            try:
                print(f"Added {topic_proposer.add(db, args.slug)}.")
            except ValueError as e:
                print(e)
                return 1
        elif args.topic_action == "repair":
            fixed = db.repair_simulated_publications()
            print(f"Requeued {len(fixed)} topic(s) with no live publication: {fixed}")
        else:
            topic_p.print_help()
        return 0

    if args.command == "assets":
        from src.config import MIN_SCENE_IMAGES
        from src.credits import approve_set, load_credits
        from src.visual_engine import MissingAssetsError, VisualEngine

        if args.assets_action == "list":
            ve = VisualEngine()
            for t in PipelineDatabase().list_all_topics():
                topic_dir = ASSETS_DIR / t["id"]
                try:
                    n = len(ve.get_scene_sequence(t))
                    state = f"ready ({n})"
                except MissingAssetsError:
                    cr = load_credits(topic_dir)
                    n = len(cr["images"])
                    state = f"needs review ({n})" if n >= MIN_SCENE_IMAGES else f"short ({n}/{MIN_SCENE_IMAGES})"
                print(f"  {state:<20} {t['id']}")
        elif args.assets_action == "approve":
            try:
                print(approve_set(ASSETS_DIR / args.topic_id, MIN_SCENE_IMAGES))
                # Said at approval: a thin topic is demoted at selection (src/topic_engine.image_gate).
                from src.topic_engine import image_gate
                gate = image_gate({"id": args.topic_id})
                if gate:
                    print(f"Note: {args.topic_id} will be made after better-stocked topics: {gate}.")
            except ValueError as e:
                print(e)
                return 1
        elif args.assets_action == "review":
            from src.image_review import review_pool
            db = PipelineDatabase()
            topics = [db.get_topic_by_id(args.topic_id)] if args.topic_id else db.list_all_topics()
            for t in topics:
                if t and (ASSETS_DIR / t["id"] / "credits.json").exists():
                    if not args.topic_id and load_credits(ASSETS_DIR / t["id"]).get("reviewed_by") == "vision":
                        continue  # already reviewed on merit; name the topic to review it again
                    s = review_pool(t, ASSETS_DIR / t["id"], force=bool(args.topic_id))  # a named topic is re-reviewed
                    print(f"{s['status']:26} {t['id']:40} kept {s['kept']}"
                          + "".join(f"\n    - {f}: {why}" for f, why in s.get("dropped", [])))
        else:
            assets_p.print_help()
        return 0

    if args.command == "health":
        res = PreFlightHealthChecker().run_all(require_live=args.live)
        _print_health(res)
        return 0 if res["all_passed"] else 1

    if args.command == "test":
        try:
            coordinator = PipelineCoordinator()
            coordinator.paid_voice_in_dry_run = args.paid_voice
            coordinator.paid_motion_in_dry_run = args.paid_motion
            refused = apply_reuse_options(coordinator, args)
            if refused:
                print(refused)
                return 1
            res = coordinator.run_cycle(args.topic_id, auto_approve=True, dry_run=True)
        except Exception:
            return 1
        print(json.dumps({k: res.get(k) for k in ("status", "master_video", "thumbnails")}, indent=2))
        print(f"Stories: {len(res.get('stories', []))}, carousel slides: {len(res.get('carousel', []))}")
        return 0 if res.get("status") in ("published", "simulated") else 1

    if args.command == "backup":
        from src.backup import backup_run
        db = PipelineDatabase()
        output_dirs = []
        if args.run_id:
            target = OUTPUT_DIR / args.run_id
            if not target.is_dir():
                print(f"Run directory not found: {target}")
                return 1
            output_dirs.append(target)
        else:
            for d in sorted(OUTPUT_DIR.iterdir()):
                if d.is_dir() and list(d.glob("*_master.mp4")):
                    output_dirs.append(d)
        if not output_dirs:
            print("No completed runs found to backup.")
            return 0
        for d in output_dirs:
            res = backup_run(d, db_path=db.db_path)
            print(f"  {d.name}: {res['status']} ({res.get('copied_count', 0)} files -> {res.get('archive_path')})")
            if res["status"] in {"failed", "skipped"}:
                return 2
            if config.BACKUP_TRANSPORT == "onedrive-api":
                print("Full recovery snapshot queued; run scripts/backupctl.py sync to verify cloud upload.")
                break
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
