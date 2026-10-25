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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config import ASSETS_DIR, CURATED_SCRIPTS_DIR, DAILY_RUN_TIME, OUTPUT_DIR, POST_SCHEDULING, TIMEZONE
from src.credits import caption_credit_line, generate_provenance_ledger, load_credits
from src.db import PipelineDatabase, QueueEmpty
from src.control import clip_words, heartbeat_text, is_paused, make_command_handler, ping_healthcheck
from src import beat_images, callouts, config, encoding, footage, graphics, motion, post_schedule, privacy, then_now
from src.llm import claude_available as llm_available
from src.scene_planner import degraded_note, documentary_cuts, plan_scenes, segment_beats
from src.topic_proposer import origin_note as topic_origin_note
from src.upscale import ensure_all, ensure_resolution
from src.music import music_for
from src.parallax import render_scenes as render_parallax
from src.video_composer import TRANS_DUR
from src.video_qa import QualityAuditor, audit_scene_feedback, qa_summary_html, review_render
from src.visual_engine import COVER_STYLES
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
MAX_FACT_REVISIONS = 2  # rewrites of a Claude script that failed fact-checking, before failing closed


MISSED_SLOT_GRACE = timedelta(hours=3)
SCHEDULED_POSTING_LOCK = "scheduled-posting"  # one post_due pass at a time across processes
CAROUSEL_GAP = timedelta(hours=48)  # Upload-Post flags similar content within 48h (docs/sources.md)


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
    """Documentary cover text: the fact-checked title over two lines (word-wrapped, at most
    max_line characters each), with an eyebrow and tagline that make no factual claim."""
    words, lines = title.upper().split(), ["", ""]
    k = 0
    for w in words:
        candidate = (lines[k] + " " + w).strip()
        if len(candidate) <= max_line:
            lines[k] = candidate
        elif k == 0 and lines[0]:
            k = 1
            lines[1] = w[:max_line]
        else:
            lines[1] = (lines[1][: max_line - 1] + "…") if lines[1] else w[: max_line - 1] + "…"
            break
    return {"eyebrow": "DOCUMENTARY", "line1": lines[0], "line2": lines[1], "tagline": "EXPLORE THE EVIDENCE"}


class PipelineCoordinator:
    def __init__(self, db: Optional[PipelineDatabase] = None, schedule_posts: bool = False):
        # schedule_posts: approved Reels wait for their posting slot (daemon); otherwise post at once (CLI).
        self.schedule_posts = schedule_posts
        self.only_promoted = False  # a dry-run daemon: booked posts fire only for runs promoted with "Post for real"
        self.paid_voice_in_dry_run = False  # `--paid-voice`: this dry run may use ElevenLabs
        self.paid_motion_in_dry_run = False  # `--paid-motion`: this dry run may buy a motion clip
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
        self.lifecycle_manager = AssetLifecycleManager()
        self.topic_engine = TopicEngine(db=self.db)
        self.script_engine = ScriptEngine()
        self.audio_engine = AudioEngine()
        self.visual_engine = VisualEngine()
        self.video_composer = VideoComposer()
        self.multiformat_gen = MultiFormatGenerator()
        self.telegram_bot = TelegramApprovalBot(db=self.db)
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
        if not health["all_passed"]:
            # Said in Telegram, not only logged: production's scheduled runs failed this way unnoticed (ffprobe).
            failed = [f"{k}: {v['msg']}" for k, v in health["details"].items() if v["required"] and not v["passed"]]
            try:
                self._alert("🩺 <b>Run not started: a pre-flight check failed</b>\n\n"
                            + "\n".join(f"• {html.escape(line[:200])}" for line in failed)
                            + "\n\nNothing was made or spent. Fix the check, then start the run again.")
            except Exception as e:
                log.warning("Could not send the pre-flight alert: %s", e)
            ping_healthcheck("/fail")
            raise SystemExit("Pre-flight health check failed; not starting a run.")
        self.lifecycle_manager.purge_intermediate_assets()

        if topic_id:
            topic = self.topic_engine.get_topic_by_id(topic_id)
            if not topic:
                raise ValueError(f"Topic ID '{topic_id}' not found in topic bank.")
        else:
            # The first topic in production order whose photo pool is approved, or can be made so now
            # (fetched and reviewed on merit); topics without usable photos are skipped, not failed.
            from src.asset_pipeline import first_ready
            topic = first_ready(self.db.ranked_topics())
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
            self._alert(f"⚠️ <b><u>TOPIC SKIPPED: FACT-STARVED</u></b>\n\n<code>{html.escape(tid)}</code>\n\n{html.escape(str(e))}\n\nSkipped gracefully to next queued topic.")
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
                self._alert(f"❌ <b><u>PRODUCTION FAILED</u></b>\n\n<b>{html.escape(self.db.run_label(run_id))}</b>\n\n{html.escape(str(e))[:600]}\n\nNothing was posted. The topic will be retried on a later run.")
            ping_healthcheck("/fail")
            raise
        finally:
            sink.stop()

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
        if not self.db.get_setting("cancel_run:" + run_id):
            return
        run = self.db.get_run(run_id)
        if not run or run["status"] in {"publishing", "publish_unknown", "published"}:
            return
        self.db.update_run(run_id, "cancelled", error="Cancelled by operator at a stage boundary")
        self.topic_engine.record_status(run["topic_id"], "cancelled", run_id=run_id)
        self._finish_progress(self.db.run_label(run_id), run_id, False, "Cancelled by operator")
        raise JobCancelled("Cancelled by operator")

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

    def _await_reason(self, run_id: str, wait_seconds: int = 120) -> Dict[str, Any]:
        """After Regenerate, waits briefly for your reason chip (and words) so the new run can act on it."""
        from src import improve
        deadline = time.monotonic() + (wait_seconds if self.telegram_bot.is_configured() else 0)
        fb = improve.operator_feedback(self.db, run_id)
        while fb is None and time.monotonic() < deadline:
            time.sleep(3)
            fb = improve.operator_feedback(self.db, run_id)
        fb = dict(fb or {})
        fb["previous"] = (self.db.get_run(run_id) or {}).get("script_payload")
        fb["from_run"] = run_id  # so later steps can read details added after the run began (_feedback)
        if fb.get("reason"):
            log.info("Regenerating with your feedback: %s %s", fb["reason"], fb.get("text", ""))
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
        """The run this regeneration replaces (Regenerate from its review), if its folder is still there."""
        previous = self._feedback().get("from_run")
        if previous and (OUTPUT_DIR / previous).is_dir():
            return OUTPUT_DIR / previous
        return None

    def _script_from_previous(self, fb: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The verified script of the run a regeneration replaces, when the review was not about the words
        (pictures, sound, cover, other): the script-stage payload as it was saved, guardrail passed. None
        when the reason concerns the script, there is no previous run, or its saved script did not pass."""
        reason = fb.get("reason")
        if not reason or reason in SCRIPT_REASONS:
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
        return saved

    BED_FILES = ("ambient_bed.wav", "ambient_bed.wav.rights.json", "music_selection.json", "sound_plan.json")

    def _seed_bed_from_previous(self, run_dir: Path, vo_path: Path) -> None:
        """A regeneration whose voiceover was carried over keeps the music bed made for it too (2 min 48 s
        to make again, 2026-10-24), unless the review was about the sound. The bed's checkpoint record is
        carried with it; the stage's own check then decides whether it is still good."""
        previous = self._previous_run_dir()
        if previous is None or (run_dir / "ambient_bed.wav").exists() or self._feedback().get("reason") == "sound":
            return
        from src.checkpoints import Checkpoints
        from src.credits import sha256_file
        try:
            same_voice = vo_path.is_file() and sha256_file(vo_path) == sha256_file(previous / "voiceover.mp3")
            record = Checkpoints(previous).read().get("bed") or {}
        except OSError:
            return
        if not same_voice or not record.get("inputs") or not all((previous / n).is_file() for n in self.BED_FILES):
            return
        try:
            for n in self.BED_FILES:
                shutil.copy2(previous / n, run_dir / n)
            Checkpoints(run_dir).adopt("bed", record["inputs"], [run_dir / n for n in self.BED_FILES])
        except (OSError, ValueError) as e:
            log.warning("Could not carry the music bed over from %s: %s", previous.name, e)
            return
        log.info("Music bed carried over from %s: made for this same voiceover.", previous.name)

    def _previous_motion_dirs(self) -> List[Path]:
        previous = self._previous_run_dir()
        return [previous / "motion"] if previous and (previous / "motion").is_dir() else []

    def _seed_voice_from_previous(self, run_dir: Path, narration: str, wanted: Optional[str] = None) -> None:
        """A regeneration for pictures, sound or a cover keeps the narration: the previous run's voiceover,
        its timing, provider record and checkpoint are copied in, so the voice stage reuses it instead of
        paying for it again (a Regenerate made a new run folder and re-voiced the same words, 2026-10-24).
        Whether a copied voice may be used is still the voice stage's rule (a test voice never goes live);
        `wanted` is the provider this run would use first, and a voice from another one is not carried."""
        previous = self._previous_run_dir()
        if previous is None or (run_dir / "voiceover.mp3").exists():
            return
        from src.checkpoints import Checkpoints
        try:
            meta = json.loads((previous / "script_metadata.json").read_text(encoding="utf-8"))
            record = Checkpoints(previous).read().get("voice") or {}
        except (OSError, ValueError):
            return
        names = ["voiceover.mp3", "words.json", "voice.json"]
        if meta.get("narration_script") != narration or not record.get("inputs") or not all((previous / n).is_file() for n in names):
            return
        try:
            made_by = (json.loads((previous / "voice.json").read_text(encoding="utf-8")) or {}).get("provider")
        except (OSError, ValueError):
            made_by = None
        if wanted and made_by and made_by != wanted:
            log.info("Voiceover not carried over from %s: it was made by %s and this run uses %s.", previous.name, made_by, wanted)
            return
        for extra in ("voiceover.mp3.rights.json",):
            if (previous / extra).is_file():
                names.append(extra)
        try:
            for n in names:
                shutil.copy2(previous / n, run_dir / n)
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
        - BBC/Discovery 16:9 intelligent compositing (blurred backdrops for vertical/square photos)
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

        def finish(success: bool, error: str = "") -> None:
            # The documentary files its findings like a Reel (it never did, so the audit never saw it).
            self._collect_findings(run_id, run_dir, tid, error=None if success else error)
            shown = False
            if card:
                try:
                    shown = bool(self.telegram_bot.finish_progress_card(card, card_title, run_id, success=success, error=error))
                except Exception as e:
                    log.debug("Could not finish the documentary progress card: %s", e)
            if not success and not shown:  # no card to turn into the failure card: say so in a message
                from src.control import short_reason
                try:
                    self._alert(f"❌ <b>Documentary failed:</b> {html.escape(card_title)}\n\n"
                                f"{html.escape(short_reason(error, 600))}\n\nNothing was posted.")
                except Exception:
                    pass

        try:
            self._check_cancel(run_id)
            # 1. Visual assets
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
            vo_path = run_dir / "voiceover.mp3"
            words_path = run_dir / "words.json"
            voice_inputs = self.voice_inputs(raw_narration, dry_run)
            licensed_req, allow_paid = voice_inputs["licensed_only"], voice_inputs["allow_paid"]
            if checkpoints.reusable("deep_voice", voice_inputs):
                words = json.loads(words_path.read_text(encoding="utf-8"))
                vo_dur = self.audio_engine.get_duration(vo_path)
                log.info("Deep-Dive Voiceover: reusing existing audio (%.2fs, %d words timed)", vo_dur, len(words))
            else:
                self._check_cancel(run_id)
                words = self.audio_engine.generate_narration(raw_narration, vo_path, licensed_only=licensed_req,
                                                             allow_paid=allow_paid)
                words_path.write_text(json.dumps(words), encoding="utf-8")
                vo_dur = self.audio_engine.get_duration(vo_path)
                log.info("Deep-Dive Voiceover: %.2fs (%d words timed)", vo_dur, len(words))
                from src.audio_provenance import record_voice
                record_voice(vo_path, self.audio_engine.last_provider, self.audio_engine.last_licensed, raw_narration)
                checkpoints.complete("deep_voice", voice_inputs,
                                     [vo_path, words_path, vo_path.with_suffix(".mp3.rights.json")])

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
            bed_inputs = {"voice_and_rules": file_inputs([vo_path, words_path, Path("src/audio_engine.py")]),
                          "cues": cue_timestamps, "music_preference": MUSIC_PREFER,
                          "selected_sources": audio_source_inputs(bed_path),
                          "library": [{k: v for k, v in t.items() if k != "uses"} for t in library_tracks],
                          "library_files": file_inputs([MUSIC_DIR / t["file"] for t in library_tracks])}
            if not checkpoints.reusable("deep_bed", bed_inputs):
                # Background score with broadcast sidechain ducking and act-boundary risers
                music = music_for(payload, vo_dur + 0.5, run_dir, self.audio_engine.voice_quota(), is_documentary=True)
                if music.get("credit"):
                    payload["music_credit"] = music["credit"]
                    payload["description"] += f"\n\n{music['credit']}"
                    payload["caption"] += f"\n\n{music['credit']}"
                    (run_dir / "script_metadata.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                from src import sound_plan as sound_plan_mod
                plan = sound_plan_mod.plan(words, topic.get("title", ""))
                (run_dir / "sound_plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
                self.audio_engine.generate_ambient_bed(
                    vo_dur + 0.5, bed_path, words=words,
                    music_path=music.get("path"), music_paths=music.get("paths"),
                    cue_timestamps=cue_timestamps, sound_plan=plan
                )
                from src.audio_provenance import record_bed
                record_bed(bed_path, music, sfx=self.audio_engine.last_sfx_sources)
                bed_inputs["selected_sources"] = audio_source_inputs(bed_path)
                checkpoints.complete("deep_bed", bed_inputs, [bed_path, bed_path.with_suffix(".wav.rights.json")])
            else:
                rights = json.loads(bed_path.with_suffix(".wav.rights.json").read_text(encoding="utf-8"))
                attribution = rights.get("required_attribution", "")
                if attribution and attribution not in payload["caption"]:
                    payload["caption"] += "\n\n" + attribution
                    payload["description"] += "\n\n" + attribution

            self._check_cancel(run_id)
            step(3, "Preparing 16:9 scenes, graphics & music")
            # 4. Visual preparation & compositing
            scene_media, _ = ensure_all(scene_media)
            hero_img = ensure_resolution(hero_img)
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
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                from src import art_direction
                doc_art = art_direction.record(run_dir, hero_img)
                payload["art_direction"] = doc_art
                fut_graphics = pool.submit(graphics.make_all, payload, topic, graphics_dir, photo=hero_img, width=1920, height=1080, palette=doc_art)
                fut_callouts = pool.submit(callouts.make, payload, topic, scene_media, graphics_dir, width=1920, height=1080)
                fut_then_now = pool.submit(then_now.make, topic, graphics_dir, width=1920, height=1080)

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
            fut_thumbs = thumb_pool.submit(lambda: (
                self.visual_engine.generate_thumbnails(hero_img, payload, run_dir, variants=False),
                self.visual_engine.generate_youtube_thumbnail(hero_img, payload, run_dir / "thumbnail_youtube_16x9.jpg")))
            thumb_pool.shutdown(wait=False)
            step(4, "Rendering the documentary, preparing review")
            try:
                result = self._render_and_certify_deep_dive(
                    topic, tid, run_id, run_dir, parent_run_id, payload, words, vo_dur, vo_path, bed_path, words_path,
                    extended_scenes, durations, cuts, master_mp4, checkpoints, file_inputs, cached_artifacts,
                    fut_thumbs, auto_approve, dry_run)
            finally:
                concurrent.futures.wait([fut_thumbs])  # never leave a writer running past the run
            finish(result.get("status") != "failed", result.get("error", ""))
            return result

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
                                      checkpoints, file_inputs, cached_artifacts, fut_thumbs, auto_approve, dry_run):
        timer = StageTimer(run_dir)
        t0 = time.perf_counter()
        cached_artifacts(run_dir, "caption_exports", {"words": words, "rules": file_inputs([Path("src/video_composer.py")])},
            lambda: [self.video_composer.export_subtitles_srt(words, master_mp4.with_suffix(".srt")),
                     self.video_composer.export_subtitles_vtt(words, master_mp4.with_suffix(".vtt"))])
        render_inputs = {"sources": file_inputs([*extended_scenes, vo_path, bed_path, words_path,
                         Path("src/video_composer.py"), Path("src/encoded_audio.py"), Path("src/encoding.py")]),
                         "durations": durations, "emphasis": topic.get("key_facts", [])[:5]}
        if checkpoints.reusable("deep_render", render_inputs):
            log.info("Master documentary video already rendered: %s", master_mp4.name)
        else:
            log.info("Composing 16:9 widescreen documentary with BBC intelligent compositing...")
            self.video_composer.compose_deep_dive(
                scene_images=extended_scenes,
                vo_path=vo_path,
                bed_path=bed_path,
                words=words,
                vo_dur=vo_dur,
                output_mp4_path=master_mp4,
                durations=durations,
                emphasis=topic.get("key_facts", [])[:5],
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

        from src.release import write_release_report, require_release
        write_release_report(self.db.get_run(run_id), dry_run=dry_run)
        if auto_approve:
            require_release(self.db.get_run(run_id), dry_run=dry_run)
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
            return produced
        if dry_run:
            self.db.set_setting("run_mode:" + produced["run_id"], "dry_run")
        return {**produced, **self.request_approval(produced["run_id"], dry_run=dry_run)}

    def _alert(self, text: str, buttons: Optional[list] = None) -> None:
        self.telegram_bot.notify(text, buttons)

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
        labels = [f.replace("_", " ") for f in changes]  # "title, carousel caption and caption"
        names = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]
        topic = self.topic_engine.get_topic_by_id(run["topic_id"]) or {"id": run["topic_id"]}
        try:
            self._verify_final_copy(payload, topic)
        except ValueError as e:
            return f"❌ Not changed: the new {names} failed the fact check.\n<i>{html.escape(str(e))[:500]}</i>"
        if not self.db.commit_review_copy(run_id, appr.get("revision", 0), payload):
            return "Review changed while checking the edit. Nothing was changed; open the current review."
        run_dir = Path(run["master_video_path"]).parent
        from src.atomic_files import write_json
        write_json(run_dir / "script_metadata.json", payload)
        from src.release import write_release_report
        report = write_release_report(self.db.get_run(run_id))
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
        decision = self.telegram_bot.wait_for_decision(run_id, self._approval_window())
        if decision == "approved":
            return {"run_id": run_id, "decision": decision, **self._after_approval(run_id, dry_run=dry_run)}
        self._settle_decision(run_id, run["topic_id"], decision)
        return {"run_id": run_id, "decision": decision, "status": decision, "topic_id": run["topic_id"]}

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

    def resume_run(self, run_id: str, auto_approve: bool = False, dry_run: bool = False) -> Dict[str, Any]:
        """
        Continues a failed run from where it stopped, reusing its approved script and
        voiceover, so a late failure (e.g. rendering) doesn't mean paying for them again.
        """
        run = self.db.get_run(run_id)
        if not run:
            raise ValueError(f"Unknown run '{run_id}'.")
        if run["status"] not in ("failed", "producing"):
            raise ValueError(f"Run status '{run['status']}' can't be resumed.")
        run_dir = OUTPUT_DIR / run_id
        if not run_dir.is_dir():
            raise ValueError(f"Run folder {run_dir} no longer exists.")
        dry_run = dry_run or self.db.get_setting("run_mode:" + run_id) == "dry_run"  # as it was made
        topic = self.topic_engine.get_topic_by_id(run["topic_id"])
        previous_status = "published" if topic["times_published"] else "queued"
        self.db.update_run(run_id, "producing")
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
            return result
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
                self._alert(f"❌ <b><u>PRODUCTION FAILED</u></b>\n\n<b>{html.escape(self.db.run_label(run_id))}</b>\n\n{html.escape(str(e))[:600]}\n\nNothing was posted. The topic will be retried on a later run.")
            ping_healthcheck("/fail")
            raise
        finally:
            sink.stop()

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
        if run["status"] == "rejected":
            return None if run.get("master_video_path") else "That run stopped before it had a video; Render the topic again instead."
        if not worth_resuming(OUTPUT_DIR / run_id, run):
            return "That run stopped before its script passed the fact check, so nothing it made is worth keeping; Render the topic again instead."
        return None

    def remake_args(self, run_id: str) -> List[str]:
        """How a run is made again: a failed one is resumed in place; a rejected one is regenerated with the
        reason you gave (a new run that carries the old one's voiceover and clips over)."""
        run = self.db.get_run(run_id) or {}
        if run.get("status") != "rejected":
            return []
        return ["--regenerate-from", run_id, "--topic-id", str(run.get("topic_id") or "")]

    # ------------------------------------------------------------ posting a dry-run result for real
    def post_now(self, run_id: str) -> str:
        """Post for real at once (operator, 2026-10-24): the Reel now, Stories and carousel booked after it.
        For a run already booked into a slot, the booking is posted now and its Stories re-timed after it."""
        run = self.db.get_run(run_id) or {}
        if run.get("status") != "scheduled":
            return self.promote_run(run_id, now=True)
        booking = next((p for p in self.db.list_scheduled_posts(("pending",), run_id=run_id) if p["kind"] == "reel"), None)
        if booking is None:
            return "⚡ No pending Reel booking for that run."
        from src.release import require_release
        try:
            require_release(run, dry_run=False)
            res = self.publish_run(run_id, dry_run=False, previous_status="published" if run.get("times_published") else "queued")
        except ValueError as e:
            return f"⚡ Not posted: {html.escape(str(e))}"
        now = datetime.now(timezone.utc)
        self.db.update_scheduled_post(booking["id"], res["status"], detail={
            "error": res.get("publish_result", {}).get("error"), "posted_at": now.isoformat()})
        if res.get("status") != "published":
            return f"⚡ Publishing ended with status '{html.escape(str(res.get('status')))}'; see the receipt above."
        for story in self.db.list_scheduled_posts(("pending",), run_id=run_id):
            if story["kind"] == "story":
                self.db.update_scheduled_post(story["id"], "pending",
                                              due_at=post_schedule.story_time(now).astimezone(timezone.utc).isoformat())
        return (f"⚡ <b>{html.escape(self.db.run_label(run_id))}</b> is posted; its Stories follow in "
                f"{config.STORY_DELAY_MINUTES} minutes, the carousel in its next slot.")

    def cancel_post(self, post_id: int) -> str:
        """Cancels a booking (operator, 2026-10-24). A Reel's Stories and carousel go with it, and the run
        is a post candidate again; a Stories or carousel booking goes alone."""
        post = next((p for p in self.db.list_scheduled_posts(("pending", "waiting")) if p["id"] == int(post_id)), None)
        if post is None:
            return "⏹ That booking is no longer pending."
        run_id = post["run_id"]
        if post["kind"] != "reel":
            self.db.update_scheduled_post(post["id"], "cancelled", detail={"reason": "cancelled by the operator"})
            return f"⏹ {post['kind'].capitalize()} booking cancelled for {html.escape(self.db.run_label(run_id))}."
        for other in self.db.list_scheduled_posts(("pending", "waiting"), run_id=run_id):
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
        slot = post_schedule.next_reel_slot(after, self._booked_reel_days(exclude_id=post["id"]))
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
        frames = files if post["kind"] == "carousel" else (files[part - 1:part] if 0 < part <= len(files) else [])
        if not frames:
            return "🔁 The frame to retry is no longer on disk."
        run = self.db.get_run(post["run_id"]) or {}
        caption = self._carousel_caption(run.get("script_payload") or {}) if post["kind"] == "carousel" else ""
        attempt = int(detail.get("retries") or 0) + 1
        platforms = sorted(failed)
        res = self.publisher.publish_photos(post["run_id"], run.get("topic_id"), post["kind"], frames, caption=caption,
                                            platforms=platforms, retry_tag=f"retry{attempt}-{'-'.join(platforms)}")
        new_detail = dict(detail, retries=attempt, last_retry={k: res.get(k) for k in ("status", "error", "post_urls")})
        if res.get("status") == "published":
            new_detail["failed"] = {}
            self.db.update_scheduled_post(post["id"], "published", detail=new_detail)
            label = html.escape(self.db.run_label(post["run_id"]))
            return f"🔁 {', '.join(platforms)} retried for {label}: posted."
        self.db.update_scheduled_post(post["id"], post["status"], detail=new_detail)
        return f"🔁 Retry on {', '.join(platforms)} ended with status '{html.escape(str(res.get('status')))}': {html.escape(str(res.get('error') or ''))[:200]}"

    def _reel_failed_platforms(self, run_id: str) -> Tuple[List[str], int]:
        """The platforms a Reel did not reach, from its publication receipts (a partial Upload-Post outcome
        names them; a merged result lists missing_platforms), and how many Reel retries were made."""
        platforms: set = set()
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
            if pub.get("status") == "unknown":
                platforms |= set((payload.get("failed") or {}).keys())
            platforms |= set(payload.get("missing_platforms") or [])
            for p in (payload.get("post_urls") or {}):
                platforms.discard(p)
        return sorted(platforms), retries

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
        from src.release import require_release
        try:
            require_release(run, dry_run=False)
        except ValueError as e:
            return f"🔁 Not retried: {html.escape(str(e))}"
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
            if run.get("status") == "publish_unknown":
                self.db.update_run(run_id, "published")
                self.topic_engine.record_status(run["topic_id"], "published", run_id=run_id)
            links = ", ".join(f"{html.escape(k)}: {html.escape(str(v))}" for k, v in (res.get("post_urls") or {}).items())
            return f"🔁 Reel re-sent to {', '.join(platforms)} for {label}: posted. {links}"
        return f"🔁 Reel retry on {', '.join(platforms)} ended with status '{html.escape(str(res.get('status')))}': {html.escape(str(res.get('error') or ''))[:200]}"

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
        # 1. Visual assets first: fail before spending on LLM/TTS calls.
        scene_media = self.visual_engine.get_scene_sequence(topic)
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
        if saved.get("guardrail_passed") and checkpoints.reusable("script", script_inputs):
            payload = saved
            log.info("Resuming with verified script inputs and intact metadata.")
        else:
            fb = self._feedback()
            if fb.get("reason") in SCRIPT_REASONS and fb.get("previous"):
                # Regenerate after your review: the previous script is revised for what you said,
                # instead of being rewritten blind.
                note = (f"The human reviewer sent the previous version back. Reason: {REASONS.get(fb['reason'], fb['reason'])}."
                        + (f" Their words: {fb['text']}" if fb.get("text") else "")
                        + " Revise the script to fix this, using only the verified facts.")
                payload = self._write_and_check_script(topic, run_id, run_dir, dry_run,
                                                       initial_payload=fb["previous"], initial_notes=note)
            else:
                kept = self._script_from_previous(fb)
                if kept is not None:
                    # The review was about pictures, sound or the cover: the script it passed stays word for
                    # word, so its voiceover carries over too (a regeneration rewrote it, 2026-10-24).
                    payload = kept
                    log.info("Script kept from %s: the review was about %s, not the words.", fb.get("from_run"), fb.get("reason"))
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

        timer.record("script_and_fact_check", time.perf_counter() - t0)
        if getattr(self, "progress_msg_id", None):
            try:
                self.telegram_bot.update_progress_card(self.progress_msg_id, 1, 5, topic["title"], run_id, "Fact check & script verified")
            except Exception:
                pass

        # 4-5. Audio (reused when resuming)
        self._check_cancel(run_id)
        vo_path = run_dir / "voiceover.mp3"
        words_path = run_dir / "words.json"
        voice_path = run_dir / "voice.json"  # which provider made it, and whether it's licensed
        voice_inputs = self.voice_inputs(payload["narration_script"], dry_run)
        # A regeneration keeps its paid voice, unless this run would be voiced by another provider now.
        self._seed_voice_from_previous(run_dir, payload["narration_script"],
                                       wanted=self.audio_engine.preferred_provider(voice_inputs["allow_paid"]))
        saved_voice = json.loads(voice_path.read_text(encoding="utf-8")) if voice_path.exists() else {}
        # A real run never reuses an unlicensed (test) voiceover. Older runs without voice.json
        # predate this record, so they are re-voiced too.
        # A narration a little over the limit is spoken slightly faster (the script stage accepted it so).
        from src.script_engine import fit_speed
        speech_speed = fit_speed(payload["narration_script"]) or 1.0
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

            def voice_at(speed):
                made = self.audio_engine.generate_narration(payload["narration_script"], vo_path, licensed_only=licensed_req,
                                                            allow_paid=allow_paid, speed=speed)
                calibrate_pace(self.audio_engine.last_provider,
                               self.audio_engine.eleven_voice if self.audio_engine.last_provider == "elevenlabs" else self.audio_engine.voice,
                               payload["narration_script"], self.audio_engine.get_duration(vo_path), speed=speed)
                return made
            words = voice_at(speech_speed)
            # A read that came out over the ceiling (ElevenLabs varies about 5% between takes: production's
            # 2026-10-25 run read 114 words in 53.7 s against 52) is made once more at the narrator's own
            # speed setting raised by the measured overrun: natural speech, not a stretched file.
            faster = refit_speed(self.audio_engine.get_duration(vo_path), speech_speed)
            if faster:
                log.info("Voiceover ran %.1fs; making it once more %.0f%% faster to fit.",
                         self.audio_engine.get_duration(vo_path), (faster - 1) * 100)
                speech_speed = faster
                voice_inputs["speed"] = faster
                words = voice_at(faster)
            words_path.write_text(json.dumps(words), encoding="utf-8")
            voice_path.write_text(json.dumps({"provider": self.audio_engine.last_provider,
                                              "licensed": self.audio_engine.last_licensed}), encoding="utf-8")
            from src.audio_provenance import record_voice
            record_voice(vo_path, self.audio_engine.last_provider, self.audio_engine.last_licensed,
                         payload["narration_script"])
            checkpoints.complete("voice", voice_inputs, [vo_path, words_path, voice_path])
            if self.audio_engine.fallback_reason:
                r_label = html.escape(self.db.run_label(run_id))
                self._alert(f"🔁 <b><u>VOICE FALLBACK</u></b>\n\n<b>{r_label}</b>\n\n{html.escape(self.audio_engine.fallback_reason)}\n\nVoiced by: <b>{html.escape(self.audio_engine.last_provider)}</b>.")
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

    @staticmethod
    def _prepare_images(scene_media, hero_img, run_dir=None):
        """Upscales low-resolution stills and de-identifies living faces (hero is one of the scenes)."""
        t0 = time.perf_counter()
        scene_media, low_res = ensure_all(scene_media)
        hero_img = ensure_resolution(hero_img)
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
        bed_inputs = {"voice_and_rules": portable_inputs(run_dir, [vo_path, words_path, Path("src/audio_engine.py")]),
                      "profile": profile.model_dump(),
                      "library": [{k: v for k, v in t.items() if k != "uses"} for t in library_tracks],
                      "library_files": file_inputs([MUSIC_DIR / t["file"] for t in library_tracks]),
                      "selected_sources": audio_source_inputs(bed_path)}
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
            music = music_for(payload, vo_dur + 0.5, run_dir, self.audio_engine.voice_quota(),
                              exclude=self._previous_tracks(self._feedback()))  # "Music / sound": not the same track
            from src import sound_plan as sound_plan_mod
            plan = sound_plan_mod.plan(words, topic.get("title", ""))  # None: word-cue fallback
            plan_path = run_dir / "sound_plan.json"
            plan_path.write_text(json.dumps(plan, indent=1), encoding="utf-8")
            self.audio_engine.generate_ambient_bed(vo_dur + 0.5, bed_path, words=words,
                                                  music_path=music["path"], sr=profile.audio_sample_rate,
                                                  loop_seamless=profile.loop_seamless, sound_plan=plan)
            if bed_path.is_file():
                record_bed(bed_path, music, sfx=self.audio_engine.last_sfx_sources)
                music_path.write_text(json.dumps(music, default=str, indent=2), encoding="utf-8")
                bed_inputs["selected_sources"] = audio_source_inputs(bed_path)
                checkpoints.complete("bed", bed_inputs, [bed_path, music_path, plan_path,
                                                         bed_path.with_suffix(".wav.rights.json")])
        payload["music_note"] = {"elevenlabs": f"🎵 Music: ElevenLabs score ({music['mood']})",
                                 "library": f"🎵 Music: free library track ({music['mood']})",
                                 "none": "🎵 Music: none available; synthesized drone used"}[music["source"]]
        if music["credit"] and music["credit"] not in payload["caption"]:  # CC BY needs attribution
            payload["caption"] = f"{payload['caption']}\n{music['credit']}"
        # music_for() counts the track's use when it picks it (a reused bed is not a new use)
        StageTimer(run_dir).record("music_and_bed", time.perf_counter() - t0)
        return self._render_approve_publish(topic, run_id, run_dir, payload, scene_media, hero_img,
                                            vo_path, bed_path, words, vo_dur, auto_approve, dry_run, previous_status,
                                            prepared)

    def _write_and_check_script(self, topic, run_id, run_dir, dry_run,
                                initial_payload=None, initial_notes="") -> Dict[str, Any]:
        tid = topic["id"]
        revision_notes, payload = initial_notes, initial_payload

        def show(text: str) -> None:  # step 1 is long; the card says which draft it is on
            if getattr(self, "progress_msg_id", None):
                self.telegram_bot.update_progress_card(self.progress_msg_id, 1, 5, topic["title"], run_id, text)
        self.script_engine.progress = show
        earlier: List[str] = []  # every draft's rejection, so a later draft can't restate one in new words
        for revision in range(MAX_FACT_REVISIONS + 1):
            self._check_cancel(run_id)
            payload = self.script_engine.generate_script(topic, revision_notes=revision_notes, previous=payload)
            passed, payload, notes = self.script_engine.verify_claims(payload, topic=topic, allow_unreviewed_curated=dry_run)
            log.info("Guardrail (draft %d): %s", revision + 1, notes)
            if passed or payload.get("origin") not in ("claude", "gemini-free"):
                break
            revision_notes = with_earlier_rejections(notes, earlier)
            earlier.append(notes)
        if not passed:
            curated = self.script_engine._load_curated(tid)
            if curated:
                pkg, reviewed = curated
                if reviewed or dry_run:
                    log.info("Claude script revisions rejected by guardrail; falling back to curated script for '%s'.", tid)
                    payload = self.script_engine._finalize(pkg, topic, origin="curated")
                    payload["human_reviewed"] = reviewed
                    passed, payload, notes = self.script_engine.verify_claims(payload, topic=topic, allow_unreviewed_curated=dry_run)

        if passed:
            credit = caption_credit_line(load_credits(ASSETS_DIR / tid))
            if credit:  # CC BY images must be attributed where the work is shared
                payload["caption"] = f"{payload['caption']}\n\n{credit}"
                if len(payload["caption"]) > 2200:
                    passed, notes = False, "Caption plus image credits exceeds Instagram's 2,200 characters."
        payload["guardrail_passed"] = passed
        # How many drafts it took and why: a run of more than two is a finding for the improvement ledger.
        payload["script_rounds"] = revision + 1
        payload["script_round_reasons"] = [r[:200] for r in earlier]
        from src.config import DATA_DIR
        sheet_file = DATA_DIR / "fact_sheets" / f"{tid}.json"
        if passed and sheet_file.is_file():
            from src.fact_evidence import record_fact_review
            record_fact_review(payload, sheet_file)
        (run_dir / "script_metadata.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        self.db.update_run(run_id, "producing", script_payload=payload)
        if not passed:
            raise RuntimeError(f"Script rejected by guardrail: {notes}")
        return payload

    def _verify_final_copy(self, payload, topic):
        from src.fact_evidence import current_fact_review, record_fact_review
        from src import trace_check, config as pipeline_config
        if current_fact_review(payload):
            return
        sheet_path = pipeline_config.DATA_DIR / "fact_sheets" / f"{topic['id']}.json"
        if not sheet_path.is_file():
            raise ValueError("Final package verification requires a fact sheet")
        sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
        passed, _, audits, notes = trace_check.check(payload, sheet)
        payload["guardrail_passed"] = passed
        payload["final_fact_audits"] = audits
        if not passed:
            raise ValueError("Final public copy failed fact verification: " + notes)
        status, confidence, evidence_audits, evidence_notes = trace_check.evidence_check(payload, sheet)
        payload["second_check"] = {"status": status, "confidence": confidence,
                                    "audits": evidence_audits, "summary": evidence_notes}
        if status != "pass":
            payload["guardrail_passed"] = False
            raise ValueError("Final public copy failed evidence verification: " + evidence_notes)
        record_fact_review(payload, sheet_path)

    def _render_approve_publish(self, topic, run_id, run_dir, payload, scene_media, hero_img,
                                vo_path, bed_path, words, vo_dur, auto_approve, dry_run, previous_status,
                                prepared=None):
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
        if low_res:
            payload["source_note"] = (f"🖼 {low_res} of {len(scene_media)} source images were below video resolution "
                                      "and were AI-upscaled; sharper originals would look better.")
            log.info(payload["source_note"])

        # Stills setup
        key = self.db.run_key(run_id)  # rotate cover layouts so neighbouring grid posts differ
        style = COVER_STYLES[int(key) % len(COVER_STYLES)] if key.isdigit() else COVER_STYLES[0]

        # 7. Master render
        self._check_cancel(run_id)
        # Match pictures to narration beats; cuts land on the beat boundaries.
        beats = segment_beats(words, vo_dur)
        # Extra candidates searched for the concrete subject of individual beats (openly licensed).
        # One query plan (picture subjects per beat) serves both beat images and real footage.
        query_plan = None
        if llm_available():
            try:
                query_plan = beat_images.plan_queries(beats, topic)
            except Exception as e:
                log.warning("Beat query planning failed (%s); no beat images or footage this run.", e)

        from src.checkpoints import Checkpoints, cached_artifacts, file_inputs
        from src.typography import FONT_FILES, FONTS_DIR
        packaging_inputs = {"photos": file_inputs([hero_img, *scene_media]), "style": style,
                            "copy": {key: payload.get(key) for key in ("title", "thumbnail", "story_slides", "carousel_slides", "poll")},
                            "rules": file_inputs([Path("src/visual_engine.py"), Path("src/multi_format.py"),
                                                  Path("src/typography.py"), Path("src/config.py"), Path("src/safe_zones.py")]),
                            "fonts": file_inputs([FONTS_DIR / name for name in FONT_FILES.values()])}
        if getattr(self, "progress_msg_id", None):
            try:
                self.telegram_bot.update_progress_card(self.progress_msg_id, 3, 5, topic["title"], run_id, "Synthesizing graphics, stills & motion")
            except Exception:
                pass

        # Concurrently generate multiformat stills (thumbnails, story, carousel)
        # and source media candidates (beat images, footage, channel graphics, callouts, then/now)
        encoding.get_video_encoder()  # probe once before parallel renderers race to detect it under load
        t0 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            fut_thumb = pool.submit(cached_artifacts, run_dir, "thumbnails", packaging_inputs,
                                    lambda: self.visual_engine.generate_thumbnails(hero_img, payload, run_dir, style=style))
            fut_story = pool.submit(cached_artifacts, run_dir, "stories", packaging_inputs,
                                    lambda: self.multiformat_gen.generate_story_sequence(hero_img, payload, run_dir, scene_images=scene_media))
            fut_carousel = pool.submit(cached_artifacts, run_dir, "carousel", packaging_inputs,
                                       lambda: self.multiformat_gen.generate_carousel_sequence(scene_media, payload, run_dir))
            # Facebook's copy of the slides, without swipe cues (it shows a multi-photo post as a grid).
            fut_carousel_fb = pool.submit(cached_artifacts, run_dir, "carousel_fb", packaging_inputs,
                                          lambda: self.multiformat_gen.generate_carousel_sequence(scene_media, payload, run_dir, facebook=True))

            fut_beats = pool.submit(beat_images.source, beats, topic, run_dir / "beat_images", plan=query_plan) if query_plan else None
            fut_footage = pool.submit(footage.source, beats, topic, run_dir / "footage", plan=query_plan) if query_plan else None
            from src import art_direction
            art = art_direction.record(run_dir, hero_img)  # the topic's accent and grade bias, recorded in palette.json
            payload["art_direction"] = art
            fut_graphics = pool.submit(graphics.make_all, payload, topic, run_dir / "graphics", photo=hero_img, palette=art)
            fut_callouts = pool.submit(callouts.make, payload, topic, scene_media, run_dir / "graphics")
            fut_then_now = pool.submit(then_now.make, topic, run_dir / "graphics")

            thumb_916, thumb_1x1 = fut_thumb.result()
            story_slides = fut_story.result()
            carousel_slides = fut_carousel.result()
            try:
                fut_carousel_fb.result()
            except Exception as e:  # Facebook then gets the Instagram slides, as before
                log.warning("Facebook carousel slides not made (%s); Facebook gets the Instagram slides.", e)

            beat_paths, beat_credits = fut_beats.result() if fut_beats else ([], [])
            footage_clips, footage_credits = fut_footage.result() if fut_footage else ([], [])
            own_graphics = fut_graphics.result()
            labelled = fut_callouts.result()
            then_clip, then_credits = fut_then_now.result()
        timer.record("stills_graphics_sourcing", time.perf_counter() - t0)

        upscaled_beats = [ensure_resolution(path) for path in beat_paths]
        for cred, safe in zip(beat_credits, privacy.anonymize_all(upscaled_beats)):
            cred["upscaled"] = str(safe)
        # The channel's own animated graphics (map, timeline, scale) as further candidates.
        own_graphics += [g for g in (labelled, then_clip) if g]
        candidates = scene_media + [Path(c["upscaled"]) for c in beat_credits] + own_graphics + footage_clips
        master_mp4 = run_dir / f"{tid}_master.mp4"
        caption_base, feedback, qa = payload["caption"], "", None
        fb = self._feedback()
        if fb.get("reason") == "pictures":
            feedback = "The human reviewer sent the previous version back for its pictures." + (
                f" Their words: {fb['text']}" if fb.get("text") else "")
        motion_memo: Dict[str, Any] = {}
        planned: List[Path] = []   # the first plan's picks, so a placement-only repair can move one
        durations: List[float] = []
        late_graphics: List[Dict[str, Any]] = []
        self._check_cancel(run_id)
        for attempt in (1, 2):  # a second pass only if the video check finds a picture mismatch
            from src.scene_planner import repair_placement
            repaired = repair_placement(planned, durations, late_graphics) if attempt == 2 and late_graphics and planned else None
            if repaired is not None:
                scenes, plan_notes = repaired, "placement repaired in place: " + "; ".join(i.get("issue", "") for i in late_graphics)
                log.info("Scene placement repaired without a second plan: %s", plan_notes[:160])
            else:
                scenes, durations, plan_notes = plan_scenes(beats, candidates, feedback=feedback)
                log.info("Scene plan: %d beats (%s).", len(beats), plan_notes[:120])
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
                scenes, durations, plan_notes = plan_scenes(beats, candidates, feedback=feedback)
            payload["caption"] = caption
            payload["scene_plan_note"] = degraded_note(plan_notes)
            used_graphics = [p.stem.replace("graphic_", "") for p in own_graphics if p in scenes]
            payload["graphics_note"] = ("🗺 Original graphics in this video: " + ", ".join(used_graphics)) if used_graphics else ""
            payload["footage_credits"] = used_footage
            payload["footage_note"] = (f"🎥 {len(used_footage)} real footage clip(s) "
                                       f"({sum(c['place_specific'] for c in used_footage)} of the actual site): "
                                       + "; ".join(f"{c['provider']}: {c['source_url']}" for c in used_footage)) if used_footage else ""
            payload["beat_image_credits"] = used
            payload["beat_image_note"] = (f"🔍 {len(used)} beat-specific image(s) auto-sourced from Wikimedia Commons; "
                                          "check they show the right thing" if used else "")
            # AI motion for the opening photo (only with a media budget; real footage and our graphics come first).
            self._check_cancel(run_id)
            scenes, motion_used = motion.apply(self.db, scenes, run_dir / "motion", memo=motion_memo, topic=topic,
                                               allow_paid=(not dry_run) or self.paid_motion_in_dry_run or config.MOTION_IN_DRY_RUNS,
                                               seed_dirs=self._previous_motion_dirs())  # a regeneration keeps its paid clips
            payload["motion_note"] = (f"🤖 AI motion ({', '.join(motion_used)}) animates {len(motion_used)} real photo(s); "
                                      "the Reel carries the AI label") if motion_used else ""
            self._check_cancel(run_id)
            # 2.5D parallax clips for the stills (local GPU); falls back to flat camera moves.
            self._progress_note(run_id, topic.get("title", run_id), 3, "Synthesizing graphics, stills & motion · animating the stills")
            t0 = time.perf_counter()
            try:
                scenes = render_parallax(scenes, durations, run_dir / "parallax", TRANS_DUR) or scenes
            except Exception as e:
                log.warning("Parallax rendering failed (%s); using flat camera moves.", e)
            timer.record(f"parallax_attempt{attempt}", time.perf_counter() - t0)
            t0 = time.perf_counter()
            cached_artifacts(run_dir, "caption_exports", {"words": words, "rules": file_inputs([Path("src/video_composer.py")])},
                lambda: [self.video_composer.export_subtitles_srt(words, master_mp4.with_suffix(".srt")),
                         self.video_composer.export_subtitles_vtt(words, master_mp4.with_suffix(".vtt"))])
            render_checkpoint = Checkpoints(run_dir / "stage_cache" / "render")
            render_inputs = {"media": file_inputs([*scenes, vo_path, bed_path]), "words": words,
                             "durations": durations, "emphasis": payload.get("emphasis_words"),
                             "grade_bias": (payload.get("art_direction") or {}).get("grade_bias") or 0.0,  # part of the master
                             "rules": file_inputs([Path("src/video_composer.py"), Path("src/config.py"),
                                                   Path("src/encoding.py"), Path("src/encoded_audio.py"), Path("src/delivery_profiles.py")]),
                             "fonts": packaging_inputs["fonts"]}
            self._check_cancel(run_id)
            if not render_checkpoint.reusable("master", render_inputs):
                self._progress_note(run_id, topic.get("title", run_id), 3, "Synthesizing graphics, stills & motion · rendering the master video")
                self.video_composer.compose_video(scenes, vo_path, bed_path, words, vo_dur, master_mp4, durations=durations,
                                                  emphasis=payload.get("emphasis_words"),
                                                  grade_bias=float((payload.get("art_direction") or {}).get("grade_bias") or 0.0))
                outputs = [master_mp4, master_mp4.with_name(master_mp4.stem + "_captions.ass"),
                           master_mp4.with_suffix(".composition.json"), master_mp4.with_suffix(".srt"), master_mp4.with_suffix(".vtt")]
                if all(path.is_file() for path in outputs):
                    render_checkpoint.complete("master", render_inputs, outputs)
            timer.record(f"master_render_attempt{attempt}", time.perf_counter() - t0)
            if getattr(self, "progress_msg_id", None):
                try:
                    self.telegram_bot.update_progress_card(self.progress_msg_id, 4, 5, topic["title"], run_id, "Video composited, preparing review")
                except Exception:
                    pass

            # The automated look (an LLM call) and the deterministic audit only read the finished
            # master and cover, so the LLM review runs while the audit measures.
            t0 = time.perf_counter()
            qa_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            fut_qa = qa_pool.submit(review_render, master_mp4, beats, thumb_916, run_dir)
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
            if not feedback or attempt == 2:
                break
            from src.scene_planner import placement_only
            late_graphics = placement_only((payload.get("self_healing") or {}).get("issues"), beat_images.picture_issues(qa))
            payload.setdefault("self_healing_actions", []).append({"attempt": attempt, "fixing": feedback.splitlines()})
            log.info("Repairing before review; re-planning scenes once:\n%s", feedback)
            # Beats whose picture doesn't show what is said get their own Commons search when the first
            # pictures did not cover them (same licence, relevance and privacy steps as the first search).
            gaps = beat_images.mismatched_beats(qa)
            if gaps:
                gap_paths, gap_credits = beat_images.source_gaps(
                    beats, gaps, topic, run_dir / "beat_images", seen={c.get("title") for c in beat_credits},
                    notes=beat_images.picture_issues(qa))
                for cred, safe in zip(gap_credits, privacy.anonymize_all([ensure_resolution(p) for p in gap_paths])):
                    cred["upscaled"] = str(safe)
                if gap_credits:
                    beat_credits += gap_credits
                    candidates += [Path(c["upscaled"]) for c in gap_credits]
                    found_for = sorted({c["beat"] for c in gap_credits})
                    feedback += ("\nNew pictures were found for beat(s) " + ", ".join(map(str, found_for))
                                 + ": use them where they show what is said.")
                    payload["self_healing_actions"][-1]["new_pictures"] = [c.get("title") for c in gap_credits]
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
        from src.release import write_release_report
        write_release_report(self.db.get_run(run_id), dry_run=dry_run)

        # 8-9. Human approval (never implicit). The production card is finished either way.
        self._finish_progress(topic.get("title", tid), run_id, success=True)
        if auto_approve:
            self.db.record_approval(run_id, tid)
            self.db.update_approval_decision(run_id, "approved", decided_by="auto-approve-flag")
            decision = "approved"
        else:
            self.telegram_bot.send_approval_request(review_mp4, thumb_916, payload, run_id)
            decision = self.telegram_bot.wait_for_decision(run_id, self._approval_window())

        result: Dict[str, Any] = {
            "run_id": run_id, "topic_id": tid, "decision": decision,
            "master_video": str(master_mp4), "thumbnails": [str(thumb_916), str(thumb_1x1)],
            "stories": [str(s) for s in story_slides], "carousel": [str(c) for c in carousel_slides],
        }
        if decision != "approved":
            self._settle_decision(run_id, tid, decision)
            result["status"] = decision
            return result

        # 10. Publish (or book the posting slot when running as the daemon)
        result.update(self._after_approval(run_id, dry_run=dry_run, previous_status=previous_status))
        return result

    def _after_approval(self, run_id: str, dry_run: bool = False, previous_status: str = "queued") -> Dict[str, Any]:
        dry_run = dry_run or self.db.get_setting("run_mode:" + run_id) == "dry_run"
        if getattr(config, "BACKUP_ENABLED", True):
            try:
                from src.backup import backup_run
                run = self.db.get_run(run_id) or {}
                if run.get("master_video_path"):
                    backup_run(Path(run["master_video_path"]).parent, db_path=getattr(self.db, "db_path", None))
            except Exception as e:
                log.warning("Tier 2 Gold Backup skipped: %s", e)
        if self.schedule_posts and POST_SCHEDULING and not dry_run and (self.db.get_run(run_id) or {}).get("content_format") != "deep_dive":
            return self.schedule_run(run_id)
        return self.publish_run(run_id, dry_run=dry_run, previous_status=previous_status)

    # ------------------------------------------------------------ posting slots
    def schedule_run(self, run_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Books an approved run's Reel into the next free slot, its Stories after it, and its
        carousel into the carousel queue. The daemon's post_due() publishes them when due."""
        now = now or datetime.now(timezone.utc)
        run = self.db.get_run(run_id)
        if not run or not run.get("master_video_path"):
            raise ValueError(f"Run '{run_id}' has no rendered video.")
        payload = run["script_payload"]
        if payload.get("origin") == "curated" and not payload.get("human_reviewed"):
            raise ValueError("This run used an unreviewed curated script; it can only be published after review.")
        from src.release import require_release
        require_release(run)
        run_dir = Path(run["master_video_path"]).parent
        slot = post_schedule.next_reel_slot(now, self._booked_reel_days())
        self.db.add_scheduled_post(run_id, "reel", slot.astimezone(timezone.utc).isoformat(), [])
        lines = [f"🎬 Reel: {post_schedule.fmt(slot)}"] + self._book_followers(run_id, run_dir, slot)
        self.db.update_run(run_id, "scheduled")
        self.topic_engine.record_status(run["topic_id"], "scheduled", run_id=run_id)
        # The review card becomes the schedule's receipt (a separate alert said the same thing again).
        if not self.telegram_bot.stamp_review_outcome(run_id, "🗓 <b>Approved · scheduled</b>\n" + "\n".join(html.escape(x) for x in lines)):
            self._alert(f"🗓 <b><u>POST SCHEDULED</u></b>\n\n<b>{html.escape(self.db.run_label(run_id))}</b>\n\n" + "\n".join(html.escape(x) for x in lines))
        return {"status": "scheduled", "scheduled_for": slot.isoformat()}

    def _book_followers(self, run_id: str, run_dir: Path, reel_slot: datetime) -> List[str]:
        """Books a Reel's Stories (STORY_DELAY_MINUTES after it) and its carousel (the next carousel slot);
        the daemon's post_due() publishes them when due. Shared by slot booking and Post now."""
        lines: List[str] = []
        stories = sorted(str(p) for p in (run_dir / "stories").glob("story_*.jpg"))
        if stories:
            when = post_schedule.story_time(reel_slot)
            self.db.add_scheduled_post(run_id, "story", when.astimezone(timezone.utc).isoformat(), stories)
            lines.append(f"📱 {len(stories)} Story frames: {post_schedule.fmt(when)}")
        carousel = sorted(str(p) for p in (run_dir / "carousel").glob("carousel_slide_*.jpg"))
        if carousel:
            self.db.add_scheduled_post(run_id, "carousel", None, carousel, status="waiting")
            lines.append(f"🖼 Carousel: queued for the next carousel slot (from {post_schedule.fmt(post_schedule.next_carousel_slot(reel_slot))})")
        return lines

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
        for post in self.db.list_scheduled_posts(("pending",)):
            due = datetime.fromisoformat(post["due_at"])
            if due > now or not self._may_post(post["run_id"]):
                continue
            run = self.db.get_run(post["run_id"]) or {}
            if post["kind"] == "reel":
                if now - due > MISSED_SLOT_GRACE:
                    self._reschedule(post, run, now)
                    continue
                res = self.publish_run(post["run_id"])
                self.db.update_scheduled_post(post["id"], res["status"], detail={
                    "error": res.get("publish_result", {}).get("error"), "posted_at": now.isoformat()})
                if res["status"] != "published":
                    for other in self.db.list_scheduled_posts(("pending", "waiting"), run_id=post["run_id"]):
                        self.db.update_scheduled_post(other["id"], "cancelled", detail={"reason": "the Reel did not post"})
            elif post["kind"] == "story":
                if run.get("status") == "scheduled":
                    continue  # its Reel was moved; the Story moved with it
                if run.get("status") != "published":
                    self.db.update_scheduled_post(post["id"], "cancelled", detail={"reason": "the Reel did not post"})
                    continue
                self._post_photos(post, run, "story")
        self._post_carousel_if_due(now)

    def _reschedule(self, post: Dict[str, Any], run: Dict[str, Any], now: datetime) -> None:
        slot = post_schedule.next_reel_slot(now, self._booked_reel_days(exclude_id=post["id"]))
        self.db.update_scheduled_post(post["id"], "pending", due_at=slot.astimezone(timezone.utc).isoformat())
        for story in self.db.list_scheduled_posts(("pending",), run_id=post["run_id"]):
            if story["kind"] == "story":
                when = post_schedule.story_time(slot).astimezone(timezone.utc).isoformat()
                self.db.update_scheduled_post(story["id"], "pending", due_at=when)
        missed = post_schedule.fmt(datetime.fromisoformat(post["due_at"]))
        self._alert(f"⏰ Missed the {html.escape(missed)} slot for {html.escape(self.db.run_label(post['run_id']))} "
                    f"(the daemon was off or paused). Moved to {html.escape(post_schedule.fmt(slot))}.")

    @staticmethod
    def _carousel_caption(payload: Dict[str, Any]) -> str:
        """The carousel's own text (Meta and Upload-Post flag duplicate captions), plus the image
        credit line the licences require. Older scripts without one fall back to the Reel caption."""
        own = (payload.get("carousel_caption") or "").strip()
        if not own:
            return payload.get("caption", "")
        credits = [ln for ln in payload.get("caption", "").splitlines() if ln.startswith(beat_images.CREDIT_PREFIX)]
        return own + ("\n\n" + credits[0] if credits else "")

    def _post_photos(self, post: Dict[str, Any], run: Dict[str, Any], kind: str) -> str:
        files = [f for f in post["files"] if Path(f).exists()]
        if not files:
            self.db.update_scheduled_post(post["id"], "failed", detail={"error": "image files are missing"})
            return "failed"
        caption = self._carousel_caption(run["script_payload"]) if kind == "carousel" else ""
        res = self.publisher.publish_photos(post["run_id"], run["topic_id"], kind, files, caption=caption)
        self.db.update_scheduled_post(post["id"], res["status"], detail=res)
        name = {"story": "Stories", "carousel": "Carousel"}[kind]
        label = html.escape(self.db.run_label(post["run_id"]))
        if res["status"] == "published":
            self._alert(f"✅ {name} posted for {label}")
        elif res["status"] == "unknown":
            self._alert(f"⚠️ <b>Not sure the {name.lower()} posted</b> for {label} "
                        f"({html.escape(str(res.get('error', '')))[:200]}). Not retried, to avoid a duplicate; please check.")
        else:
            self._alert(f"❌ {name} not posted for {label}: {html.escape(str(res.get('error', '')))[:300]}")
        return res["status"]

    def _reel_posted_before(self, run_id: str, cutoff: datetime) -> bool:
        """Upload-Post flags similar content within 48h per account, so a topic's carousel waits."""
        for p in self.db.list_scheduled_posts(("published",), run_id=run_id):
            if p["kind"] == "reel":
                return datetime.fromisoformat(p["detail"].get("posted_at") or p["updated_at"]) <= cutoff
        return True  # posted outside the scheduler (CLI): no slot record to wait on

    def _post_carousel_if_due(self, now: datetime) -> None:
        local = now.astimezone(post_schedule.tz())
        slot = post_schedule.carousel_time(local.date())
        if not slot or not slot <= now < slot + MISSED_SLOT_GRACE:
            return
        if self.db.get_setting("carousel_posted_on") == local.date().isoformat():
            return
        waiting = [p for p in self.db.list_scheduled_posts(("waiting",)) if self._may_post(p["run_id"])]
        for post in waiting:  # a week-old carousel is stale next to newer Reels
            if now - datetime.fromisoformat(post["created_at"]) > timedelta(days=7):
                self.db.update_scheduled_post(post["id"], "expired")
        ready = [(p, self.db.get_run(p["run_id"])) for p in waiting
                 if now - datetime.fromisoformat(p["created_at"]) <= timedelta(days=7)]
        ready = [(p, r) for p, r in ready if r and r["status"] == "published"
                 and self._reel_posted_before(p["run_id"], now - CAROUSEL_GAP)]
        if not ready:
            return
        post, run = max(ready, key=lambda pr: pr[0]["id"])  # the newest topic
        self.db.set_setting("carousel_posted_on", local.date().isoformat())  # before posting: never twice a day
        self._post_photos(post, run, "carousel")

    def publish_run(self, run_id: str, dry_run: bool = False, previous_status: str = "queued") -> Dict[str, Any]:
        from src.publication_lock import publication_lock
        with publication_lock(self.db.db_path, run_id, wait=30):
            return self._publish_run_locked(run_id, dry_run, previous_status)

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
            raise ValueError("This run used an unreviewed curated script; it can only be published after review.")
        from src.release import require_release
        require_release(run, dry_run=dry_run)
        envelope = self.publisher.build_envelope(
            run_id, Path(run["master_video_path"]), Path(run["thumbnail_cover_path"]),
            Path(run["thumbnail_grid_path"]), payload,
            platforms=["youtube"] if profile == "deep_dive_16x9" else None,
        )
        if dry_run:
            self.db.update_run(run_id, "approved")
        elif not self.db.claim_publication(run_id):
            raise ValueError("Publication was already claimed or this run is not ready")
        try:
            pub = self.publisher.publish_reel(envelope, dry_run=dry_run)
        except Exception as exc:
            if not dry_run:
                self.db.update_run(run_id, "publish_unknown", error=type(exc).__name__)
                self.topic_engine.record_status(tid, "publish_unknown", run_id=run_id)
                self.db.record_publication(tid, run_id, "coordinator", "unknown",
                                           {"error_type": type(exc).__name__})
            raise
        status = pub["status"]

        if status == "published":
            self.db.update_run(run_id, "published")
            self.topic_engine.record_status(tid, "published", run_id=run_id)
            log.info("Published %s.", run_id)
            urls = pub.get("post_urls") or pub.get("post_ids") or {}
            links = "\n".join(f"{html.escape(k)}: {html.escape(str(v))}" for k, v in urls.items())
            missing = pub.get("missing_platforms")
            warn = (f"\n⚠️ Not posted to {html.escape(', '.join(missing))}: {html.escape(str(pub.get('error', '')))[:300]}"
                    if missing else "")
            self._alert(f"✅ <b><u>PUBLISHED</u></b>\n\n<b>{html.escape(payload.get('title', tid))}</b>\n\n{links}{warn}")
        elif status == "simulated":
            self.db.update_run(run_id, "simulated")
            self.topic_engine.record_status(tid, previous_status, run_id=run_id)
        elif status == "blocked_unlicensed":
            self.db.update_run(run_id, "approved", error=pub.get("error"))
            self.topic_engine.record_status(tid, "approved", run_id=run_id)
            log.warning("Publishing halted for %s: commercial voice license required.", run_id)
            key = self.db.run_key(run_id)
            self._alert(
                f"🛑 <b><u>BROADCAST BLOCKED: UNLICENSED VOICE</u></b>\n\n"
                f"Topic: <b>{html.escape(payload.get('title', tid))}</b>\n"
                f"Voice used: <code>{html.escape(str(pub.get('provider', 'unlicensed')))}</code> (Unlicensed)\n\n"
                f"Meta Content Monetization Program (CMP) policies prohibit commercial publishing without a commercial license.\n\n"
                f"👉 <i>Options:</i>\n"
                f"• Configure an active Azure Speech key or ElevenLabs paid plan\n"
                f"• Or execute a simulated publish for testing",
                [
                    {"text": "🧪 Simulate Publish Only", "callback_data": f"simpub:{key}"},
                    {"text": "📨 Re-send Review Card", "callback_data": f"resend:{key}"}
                ]
            )
            self._stamp_review(run_id, status, pub)
            return {"status": status, "publish_result": pub}
        elif status == "unknown":
            self.db.update_run(run_id, "publish_unknown", error=pub.get("error"))
            self.topic_engine.record_status(tid, "publish_unknown", run_id=run_id)
            log.error("Publish outcome UNKNOWN for %s. Check the accounts, then `topic requeue %s` if it did not post.", run_id, tid)
            key = self.db.run_key(run_id)
            self._alert(
                f"⚠️ <b><u>PUBLISH UNCONFIRMED</u></b>\n\n<b>{html.escape(self.db.run_label(run_id))}</b>\n\n"
                f"The upload may have gone through ({html.escape(str(pub.get('error') or 'no confirmation received'))[:200]}). "
                "I won't retry automatically, to avoid a duplicate post. Check Facebook and Instagram, then tell me:",
                [{"text": "✅ It posted", "callback_data": f"pubok:{key}"},
                 {"text": "🔁 It didn't post", "callback_data": f"pubno:{key}"}])
        else:
            self.db.update_run(run_id, "failed", error=pub.get("error"))
            self.topic_engine.record_status(tid, "failed", run_id=run_id)
            log.error("Publishing failed for %s: %s", run_id, pub.get("error"))
            self._alert(f"❌ <b><u>PUBLISH FAILED</u></b>\n\n<b>{html.escape(self.db.run_label(run_id))}</b>\n\n{html.escape(str(pub.get('error') or 'no error details were returned'))[:500]}\n\nNothing was posted. The topic goes back in the queue for a retry.")
        if config.YOUTUBE_SHORTS and profile == "reel_9x16" and status in ("published", "simulated"):
            pub["youtube_short"] = short = self.publisher.publish_short(envelope, dry_run=dry_run)
            if short["status"] == "published":
                links = "\n".join(html.escape(str(v)) for v in short.get("post_urls", {}).values())
                self._alert(f"▶️ <b>YouTube Short posted</b> ({html.escape(config.YOUTUBE_SHORTS_PRIVACY)})\n"
                            f"<b>{html.escape(payload.get('title', tid))}</b>\n{links}")
            elif short["status"] in ("failed", "unknown"):
                # The Reel stands either way; an unknown Short is checked by hand, never re-sent blindly.
                self._alert(f"⚠️ <b>YouTube Short {'unconfirmed' if short['status'] == 'unknown' else 'not posted'}</b>\n"
                            f"<b>{html.escape(payload.get('title', tid))}</b>: the Reel is unaffected.\n"
                            f"{html.escape(str(short.get('error', '')))[:300]}")
        if dry_run and profile == "reel_9x16" and status == "simulated":
            # A live approval books the Stories and the carousel for their slots; a dry run skipped them
            # entirely, so it never tested them. They are simulated here, each request as it would be sent.
            run_dir = Path(run["master_video_path"]).parent
            stories = sorted(str(p) for p in (run_dir / "stories").glob("story_*.jpg"))
            slides = sorted(str(p) for p in (run_dir / "carousel").glob("carousel_slide_*.jpg"))
            if stories:
                pub["stories"] = self.publisher.publish_photos(run_id, tid, "story", stories, dry_run=True)
            if slides:
                pub["carousel"] = self.publisher.publish_photos(run_id, tid, "carousel", slides,
                                                                caption=self._carousel_caption(payload), dry_run=True)
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
            elif status == "published":
                urls = dict(pub.get("post_urls") or {})
                for url in ((pub.get("youtube_short") or {}).get("post_urls") or {}).values():
                    urls["youtube short"] = url
                text = "✅ <b>Approved · posted</b>" + "".join(f"\n• {html.escape(k)}: {html.escape(str(v))}" for k, v in urls.items())
            elif status == "unknown":
                text = ("⚠️ <b>Approved · publish unconfirmed</b>: check the accounts, then answer the "
                        "PUBLISH UNCONFIRMED alert (It posted / It didn't post).")  # it sits above the panel
            elif status == "blocked_unlicensed":
                text = "🛑 <b>Approved · blocked</b>: the voice has no commercial licence; nothing was posted."
            else:
                text = "❌ <b>Approved · not posted</b>: " + html.escape(str(pub.get("error") or status))[:300]
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


def _write_result(path: Optional[str], result: Dict[str, Any]) -> None:
    """The worker's outcome for the daemon that started it (never fails the run)."""
    if not path:
        return
    try:
        Path(path).write_text(json.dumps(result, default=str), encoding="utf-8")
    except OSError as e:
        log.warning("Could not write the run result to %s: %s", path, e)


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


REMAKE_STATUSES = ("failed", "rejected")  # runs with a video that Re-render makes again without paying twice
PROMOTABLE_STATUSES = ("simulated", "approved")  # a finished dry run that "Post for real" may publish
PROMOTED_PREFIX = "promoted:"  # setting: the run was promoted to real posting (and when)


def regeneration_topic(db, previous: str) -> Optional[str]:
    """The topic a regeneration is for: the one the run it replaces was made for."""
    return (db.get_run(previous) or {}).get("topic_id")


def rerender_started(db, run_id: str) -> str:
    run = db.get_run(run_id) or {}
    how = ("made again with the reason you gave" if run.get("status") == "rejected" else "re-rendered")
    return (f"🔁 <b>{html.escape(db.run_label(run_id))}</b> is being {how}: its script, voiceover and accepted "
            "motion clips are reused, so nothing already paid for is bought again. A new review card comes when it's ready.")


def run_in_worker(content_format: str, dry_run: bool, schedule_posts: bool, timeout: Optional[float] = None,
                  extra: Optional[List[str]] = None, resume: Optional[str] = None) -> Dict[str, Any]:
    """Runs one production cycle in its own process (main.py run), as the development bot already does.
    A native crash in a run (2026-10-23: a GPU library brought a worker down) then costs that run only,
    not the daemon with its bot and schedule. The daemon's poller keeps handling Telegram; the worker
    reads decisions from the database (--background-polling). Returns the worker's result, with
    "crashed" (and the exit code) when it ended without writing one."""
    import tempfile
    fd, result_path = tempfile.mkstemp(prefix="rr-run-", suffix=".json")
    os.close(fd)
    # `resume`: re-render that failed run in place (its script, voice and clips are reused).
    command = ["resume", resume] if resume else ["run", "--require-live", "--format", content_format]
    cmd = ([sys.executable, "-u", "main.py", *command, "--background-polling", "--result-file", result_path]
           + (["--dry-run"] if dry_run else []) + (["--schedule-posts"] if schedule_posts else []) + list(extra or []))
    try:
        proc = subprocess.run(cmd, cwd=str(config.BASE_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
        try:
            result = json.loads(Path(result_path).read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            result = {}
        if not result:  # no result written: the process died (a native crash ends it without one)
            result = {"status": "crashed", "exit_code": proc.returncode,
                      "stderr_tail": "\n".join((proc.stderr or "").strip().splitlines()[-8:])}
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
        text = (f"⚠️ <b>Release {tag} was not installed</b>: {html.escape(str(receipt.get('reason') or 'its checks failed'))[:400]}. "
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
    bot.notify(text)
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
        db.update_run(run_id, "failed", error="Interrupted: the process making it ended (crash or stop) before it finished.")
        db.clear_active_progress(run_id)
        if topic_engine is not None and row["topic_id"]:
            topic_engine.record_status(row["topic_id"], "queued", run_id=run_id)
        log.warning("Run %s was interrupted (its process ended); marked failed, topic back in the queue.", run_id)
        done.append(run_id)
    return done


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
    for run_id in runs:
        log.info("Resuming the review wait for %s.", run_id)
        follow(run_id, resend=False)
    return runs


def _report_interrupted(coordinator, bot) -> None:
    try:
        stale = reconcile_interrupted_runs(coordinator.db, coordinator.topic_engine)
    except Exception as e:
        log.warning("Could not reconcile interrupted runs: %s", e)
        return
    if stale:
        bot.notify("⚠️ <b>Interrupted run(s) cleared</b>: the process making them ended before they finished. "
                   "Nothing was posted; the topics are back in the queue.\n"
                   + "\n".join(f"• {html.escape(coordinator.db.run_label(r))}" for r in stale))


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
            except Exception as e:
                log.exception("Posting scheduled items failed: %s", e)
                bot.notify(f"⚠️ Scheduled posting hit an error: {html.escape(str(e))[:300]}")

    thread = threading.Thread(target=tick, name="post-due", daemon=True)
    thread.start()
    return thread


def _proposal_trigger(db: PipelineDatabase, bot) -> Any:
    """Research is an operator job: cancellable and tied to its originating panel."""
    def work():
        from src import topic_proposer
        proposals = topic_proposer.propose(db)
        text = (f"💡 Research complete: {len(proposals)} new idea(s). Open Waiting ideas to review them."
                if proposals else "💡 No new verified topic ideas this time.")
        return text, [[{"text": "Waiting ideas", "callback_data": "cprop:"}],
                      [{"text": "← Topic ideas", "callback_data": "cideas:"}],
                      [{"text": "← Dashboard", "callback_data": "cdash:"}]]

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
            regenerations = 0
            previous = getattr(args, "regenerate_from", None)
            if previous:  # a Regenerate decided outside a running cycle (Resend, or after a restart)
                coordinator._operator_feedback = coordinator._await_reason(previous, wait_seconds=10)
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
                        "Nothing new was made; Render the topic again from the queue for a fresh start.")
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
            res = coordinator.resume_run(args.run_id, args.auto_approve, args.dry_run)
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
            TelegramApprovalBot(db=db).notify(text)
        return 0

    if args.command == "update":
        import re
        from src import maintenance
        plan = maintenance.check()
        print(re.sub(r"<[^>]+>", "", maintenance.report_text(plan)))
        if args.apply:
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
                review_waits.add(run_id)
                try:
                    result = coordinator.request_approval(run_id, resend=resend)
                except Exception as e:
                    bot.notify(f"⚠️ Couldn't {'re-send' if resend else 'resume'} the review: {html.escape(str(e))[:300]}")
                    return
                finally:
                    review_waits.discard(run_id)
                if result.get("decision") == "regenerate":
                    run = coordinator.db.get_run(run_id) or {}
                    started = launch_worker(run.get("content_format") or "reel",
                                            ["--topic-id", result["topic_id"], "--regenerate-from", run_id])
                    bot.notify(f"🔄 <b>Regenerating</b> {html.escape(coordinator.db.run_label(run_id))} with your feedback. {started}")
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
                return f"A production run is already in progress{pid_info}. Watch Telegram for progress updates."

            def monitor_worker(proc):
                with run_lock:
                    log.info("Isolated production worker PID %d started.", proc.pid)
                    stdout, stderr = proc.communicate()
                    rc = proc.returncode
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
                    if cleared:
                        bot.notify(f"💥 <b>The run's worker process ended without finishing</b> (exit {rc}"
                                   + (f" / 0x{rc & 0xFFFFFFFF:08X}" if isinstance(rc, int) and rc else "")
                                   + "). Nothing was posted; the run was cleared and its topic is back in the queue.")
                    # A batch (/produce N): the next episode starts once this worker has ended cleanly, which
                    # is after your decision on its review; a crash ends the batch.
                    try:
                        remaining = int(getattr(db, "get_setting", lambda key: None)(PRODUCE_KEY) or 0)
                    except (TypeError, ValueError, AttributeError):
                        remaining = 0
                    if remaining > 0 and rc == 0 and not cleared:
                        db.set_setting(PRODUCE_KEY, str(remaining - 1))
                        threading.Timer(5, launch_worker).start()  # after this lock is released
                        bot.notify(f"🎬 Next episode of the batch is starting ({remaining - 1} more after it).")
                    elif remaining > 0:
                        db.set_setting(PRODUCE_KEY, "0")
                        bot.notify("⏹ Batch production stopped: the last episode did not finish cleanly.")

            from src.config import BASE_DIR
            # With posting slots on, the worker books the approved Reel like the daemon does.
            command = ["resume", resume] if resume else ["run", "--require-live", "--format", content_format]
            worker_proc = subprocess.Popen(
                [sys.executable, "-u", "main.py", *command, "--background-polling"]
                + (["--dry-run"] if config.BOT_RUNS_DRY else [])  # nothing posts from a bot-started run by default
                + (["--schedule-posts"] if POST_SCHEDULING else []) + list(extra or []),
                cwd=str(BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            threading.Thread(target=monitor_worker, args=(worker_proc,), name="worker-monitor", daemon=True).start()
            return f"🎬 Production run launched in isolated worker (PID {worker_proc.pid}). Live progress updates will stream here."

        def rerender_in_bot(run_id: str) -> str:
            refusal = coordinator.rerender_refusal(run_id)
            if refusal:
                return f"🔁 {html.escape(refusal)}"
            extra = coordinator.remake_args(run_id)
            started = launch_worker(extra=extra) if extra else launch_worker(resume=run_id)
            return started if "already in progress" in started else rerender_started(db, run_id)

        PRODUCE_KEY = "produce_remaining"  # episodes still to start in a /produce batch

        def produce_batch(n: int) -> str:
            """/produce N (operator, 2026-10-24: a week of episodes in one go): the next N queued topics,
            one worker at a time; each comes to review, and the next starts once that worker has ended
            cleanly, i.e. after your decision."""
            db.set_setting(PRODUCE_KEY, str(max(0, n - 1)))
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
            send_photo=bot.send_photo, send_audio=bot.send_audio, send_message=lambda text, buttons=None: bot.notify(text, buttons=buttons, record_alert=False), delete_message=bot.delete_message, voice_quota=coordinator.audio_engine.voice_quota,
            propose_topics=_proposal_trigger(db, bot),
            run_actions={"pubok": coordinator.confirm_posted,
                         "pubno": coordinator.confirm_not_posted,
                         "resend": resend_for_review,
                         "edit": getattr(coordinator, "edit_copy", None),
                         "simpub": coordinator.simulate_publish,
                         "rerender": rerender_in_bot,
                         "promote": getattr(coordinator, "promote_run", None),
                         "postnow": getattr(coordinator, "post_now", None),
                         "cancelpost": getattr(coordinator, "cancel_post", None),
                         "movepost": getattr(coordinator, "move_post", None),
                         "retrypost": getattr(coordinator, "retry_post", None),
                         "retryreel": getattr(coordinator, "retry_reel", None)},
            produce_batch=produce_batch,
        )
        if POST_SCHEDULING:  # what this mode books, it also posts when due
            coordinator.only_promoted = bool(config.BOT_RUNS_DRY)  # bot-started runs are dry: only promoted ones post
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
            bot.notify("🔴 <b>Studio Control Center Offline</b> • Service stopped.", record_alert=False)
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
        release_note = None  # after an installed release: the notice that opens the one dashboard panel
        try:  # leases left by the previous process; the schedule the release deployer keeps clear of
            clear_busy_leases(coordinator.db)
            coordinator.db.set_setting("daemon_schedule", json.dumps({"time": args.time, "timezone": TIMEZONE}))
            release_note = announce_release(coordinator.db, bot, startup=True)
        except Exception as e:
            log.warning("Could not record the daemon's release state: %s", e)
        bot.register_commands()
        run_lock = threading.Lock()  # one production run at a time (daily job or /run)

        PRODUCE_KEY = "produce_remaining"  # episodes still to start in a /produce batch

        def continue_batch(status: Optional[str]) -> None:
            """/produce N in the daemon (operator, 2026-10-25: "bot says I don't know /produce"; production runs the
            daemon, where the batch was never wired): the next episode starts once this run's worker has ended
            cleanly, which is after the operator's decision on its review; a crash or an empty queue ends the batch."""
            try:
                remaining = int(coordinator.db.get_setting(PRODUCE_KEY) or 0)
            except (TypeError, ValueError, AttributeError):
                remaining = 0
            if remaining <= 0:
                return
            if status in ("published", "simulated", "rejected", "scheduled"):
                coordinator.db.set_setting(PRODUCE_KEY, str(remaining - 1))
                scheduler.add_job(production_run, kwargs={"manual": True})
                bot.notify(f"🎬 Next episode of the batch is starting ({remaining - 1} more after it).")
            else:
                coordinator.db.set_setting(PRODUCE_KEY, "0")
                bot.notify("⏹ Batch production stopped: the last episode did not finish cleanly.")

        def production_run(manual: bool = False, content_format: str = "reel", extra: Optional[List[str]] = None,
                           resume: Optional[str] = None):
            if not manual and is_paused(coordinator.db):
                log.info("Scheduled run skipped: paused.")
                bot.notify("⏸ Scheduled run skipped (paused). Send /resume to re-enable.",
                           transient=True, ttl_minutes=23 * 60 + 50)
                return
            if not run_lock.acquire(blocking=False):
                log.warning("A run is already in progress; skipping.")
                return
            status = None
            try:
                # Each run in its own process: a crash costs the run, never the daemon (its bot and schedule).
                with busy_lease(coordinator.db, "the production run"):
                    result = run_in_worker(content_format, dry_run=args.dry_run, schedule_posts=POST_SCHEDULING, extra=extra,
                                           resume=resume)
                status = result.get("status")
                if status in ("published", "simulated", "rejected", "scheduled"):
                    ping_healthcheck()
                elif status == "queue_empty":
                    ping_healthcheck()  # the system is healthy; it just has nothing new to make
                    if "timed-out review" in str(result.get("detail", "")):  # held, not exhausted
                        bot.notify("⌛ <b>Today's run was skipped</b>: every topic left waits for a timed-out review. "
                                   "Resend or Reject it.", [{"text": "🎬 Reviews", "callback_data": "creviews:"}])
                    else:
                        bot.notify("📭 <b>Topic queue is empty</b>: every topic has been published, so today's run was skipped "
                                   "(repeating topics risks Meta's unoriginal-content rules).",
                                   [{"text": "💡 Propose new topics", "callback_data": "propose:"}])
                elif status == "refused":  # a re-render the run's state no longer allows
                    bot.notify(f"🔁 <b>Re-render not started</b>: {html.escape(str(result.get('error', '')))}")
                elif status == "crashed":
                    code = result.get("exit_code")
                    log.error("Run worker crashed (exit %s); the daemon carries on.\n%s", code, result.get("stderr_tail", ""))
                    cleared = reconcile_interrupted_runs(coordinator.db, coordinator.topic_engine)
                    bot.notify(f"💥 <b>The run's worker process crashed</b> (exit {html.escape(str(code))}"
                               + (f" / 0x{int(code) & 0xFFFFFFFF:08X}" if isinstance(code, int) else "")
                               + "). Nothing was posted; the daemon, its bot and the schedule are unaffected."
                               + (" The run was cleared and its topic is back in the queue." if cleared else ""))
            except BaseException as e:  # keep the daemon alive; the run is already recorded and alerted
                log.exception("Run failed: %s", e)
            finally:
                run_lock.release()
                try:
                    continue_batch(status)
                except Exception as e:
                    log.warning("Batch continuation failed: %s", e)

        def produce_batch(n: int) -> str:
            coordinator.db.set_setting(PRODUCE_KEY, str(max(0, n - 1)))
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
            # One a day, gone before the next one (it stacked up with no way back to the panel otherwise).
            bot.notify(heartbeat_text(coordinator.db, next_run(), coordinator.audio_engine.voice_quota()),
                       buttons=[{"text": "← Dashboard", "callback_data": "cdash:"}], silent=True,
                       transient=True, ttl_minutes=23 * 60 + 50, record_alert=False)
            ping_healthcheck()

        scheduler.add_job(heartbeat, CronTrigger(hour=hb_hour, minute=hb_minute, timezone=TIMEZONE),
                          max_instances=1, coalesce=True, misfire_grace_time=3600)

        def post_due():
            if is_paused(coordinator.db):
                return  # held; slots missed while paused move to the next free slot on resume
            try:
                coordinator.post_due()
            except Exception as e:
                log.exception("Posting scheduled items failed: %s", e)
                bot.notify(f"⚠️ Scheduled posting hit an error: {html.escape(str(e))[:300]}")

        def report_release():  # a release refused before the stop window causes no restart to announce it
            try:
                announce_release(coordinator.db, bot)
            except Exception as e:
                log.debug("Release receipt check failed: %s", e)

        scheduler.add_job(report_release, "interval", minutes=5, max_instances=1, coalesce=True)

        def watch_deployer():  # a release about to install is announced before the bot goes quiet for it
            try:
                from src.control import deployer_notice
                notice = deployer_notice(coordinator.db)
                if notice:
                    bot.notify(notice, silent=True, record_alert=False)
            except Exception as e:
                log.debug("Deployer status check failed: %s", e)

        scheduler.add_job(watch_deployer, "interval", seconds=20, max_instances=1, coalesce=True)

        if POST_SCHEDULING:  # approved Reels are booked into slots; this publishes them when due
            coordinator.only_promoted = bool(args.dry_run)  # a dry-run daemon posts only runs promoted with "Post for real"
            scheduler.add_job(post_due, "interval", minutes=1, max_instances=1, coalesce=True)

        from src import maintenance

        def weekly_update_check():
            try:
                plan = maintenance.check()
            except Exception as e:
                bot.notify(f"🧰 Weekly update check failed: {html.escape(str(e))[:300]}")
                return
            buttons = [{"text": "🧰 Apply updates", "callback_data": "upd:apply"}] if plan.applicable else None
            if buttons:
                bot._api("sendMessage", data={"chat_id": bot.chat_id, "parse_mode": "HTML",
                                              "text": maintenance.report_text(plan),
                                              "reply_markup": json.dumps({"inline_keyboard": [buttons]})})
            else:
                bot.notify(maintenance.report_text(plan), transient=True, ttl_minutes=10070)  # until next week's

        scheduler.add_job(weekly_update_check, CronTrigger(day_of_week="mon", hour=hb_hour, minute=hb_minute + 5 if hb_minute < 55 else 0,
                                                           timezone=TIMEZONE), max_instances=1, coalesce=True, misfire_grace_time=3600)

        from src import policy_watch

        def weekly_policy_watch():
            try:
                bot.notify(policy_watch.digest_text(policy_watch.check(coordinator.db)), transient=True, ttl_minutes=10070)
            except Exception as e:
                bot.notify(f"📜 Weekly policy watch failed: {html.escape(str(e))[:300]}")

        scheduler.add_job(weekly_policy_watch, CronTrigger(day_of_week="wed", hour=hb_hour, minute=hb_minute,
                                                           timezone=TIMEZONE), max_instances=1, coalesce=True,
                          misfire_grace_time=3600)

        review_waits: set = set()  # runs this process is waiting on (the sweep must not start a second)

        def follow_review(run_id: str, resend: bool) -> None:
            """Waits on a review in the background and acts on its decision; a Regenerate becomes a
            production run of the same topic, in its worker, guided by the reason given."""
            def work():
                review_waits.add(run_id)
                try:
                    result = coordinator.request_approval(run_id, dry_run=args.dry_run, resend=resend)
                except Exception as e:
                    bot.notify(f"⚠️ Couldn't {'re-send' if resend else 'resume'} the review: {html.escape(str(e))[:300]}")
                    return
                finally:
                    review_waits.discard(run_id)
                if result.get("decision") == "regenerate":
                    run = coordinator.db.get_run(run_id) or {}
                    scheduler.add_job(production_run, kwargs={
                        "manual": True, "content_format": run.get("content_format") or "reel",
                        "extra": ["--topic-id", result["topic_id"], "--regenerate-from", run_id]})
                    bot.notify(f"🔄 <b>Regenerating</b> {html.escape(coordinator.db.run_label(run_id))} with your feedback; "
                               "its progress card follows.")
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
                    from src.credits import load_credits
                    lines = [f"• {html.escape(t['title'])}: {html.escape(load_credits(config.ASSETS_DIR / t['id']).get('review_note') or 'no usable photos yet')}"
                             for t in not_ready]
                    bot.notify("🖼 <b><u>PHOTO POOLS NOT READY</u></b>\n\nThese upcoming topics will be skipped until "
                               "they have approved photos:\n" + "\n".join(lines), silent=True)
            except Exception as e:
                log.warning("Nightly photo pool preparation failed: %s", e)
            for t in prebuild_candidates(coordinator.db, 3):
                try:
                    if not fact_sheet.load(t["id"]):
                        fact_sheet.build(t)
                except Exception as e:
                    log.warning("Fact sheet pre-build failed for %s: %s", t["id"], e)
                    bot.notify(f"🧾 Couldn't build the fact sheet for {html.escape(t['title'])}: {html.escape(str(e))[:200]}")

        scheduler.add_job(prebuild_fact_sheets, CronTrigger(hour=3, minute=0, timezone=TIMEZONE),
                          max_instances=1, coalesce=True, misfire_grace_time=6 * 3600)
        scheduler.add_job(propose_topics.run_now, CronTrigger(day_of_week="fri", hour=hb_hour, minute=hb_minute,
                                                              timezone=TIMEZONE), max_instances=1, coalesce=True,
                          misfire_grace_time=3600)

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
                               silent=True)
            except Exception as e:
                log.warning("Account check failed: %s", e)
            try:
                for p in improve.make_proposals(coordinator.db):
                    if p["status"] == "ready":
                        text, buttons = improve.card(p)
                        bot.notify(text, buttons=buttons, silent=True, record_alert=False)
                if improve.proposals(coordinator.db, "testing") and run_lock.acquire(blocking=False):
                    try:
                        from scripts.eval_prompts import run_candidates
                        rows = run_candidates(coordinator.db)
                    finally:
                        run_lock.release()
                    for p in improve.update_gates(coordinator.db, rows):
                        if p["status"] == "ready":
                            text, buttons = improve.card(p)
                            bot.notify(text, buttons=buttons, silent=True, record_alert=False)
            except Exception as e:
                # Said in Telegram: the cycle failed every night on production for days as a log line only
                # (2026-10-24). A broken improvement loop is itself something that needs you.
                log.warning("Improvement cycle failed: %s", e)
                from src.control import short_reason
                bot.notify("🧭 <b>Improvement cycle failed</b> tonight, so no findings were turned into proposals.\n"
                           f"<blockquote expandable>{html.escape(short_reason(str(e), 600))}</blockquote>", silent=True)

        def weekly_digest():
            from src import improve
            try:
                bot.notify(improve.digest_text(coordinator.db), silent=True, transient=True, ttl_minutes=10070)
            except Exception as e:
                log.warning("Weekly digest failed: %s", e)

        def collect_audience():
            from src import audience
            try:
                n = audience.collect(coordinator.db)
                if n:
                    log.info("Audience metrics: %d snapshot(s) stored.", n)
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
        scheduler.add_job(collect_audience, CronTrigger(hour="*/6", minute=20, timezone=TIMEZONE),
                          max_instances=1, coalesce=True, misfire_grace_time=3 * 3600)

        def check_accounts_now():  # so status and the dashboard show real account state from the start
            try:
                from src import accounts
                accounts.check(coordinator.db, force=True)
            except Exception as e:
                log.warning("Account check failed: %s", e)
        threading.Thread(target=check_accounts_now, name="account-check", daemon=True).start()

        def apply_updates() -> str:
            if run_lock.locked():
                return "A production run is in progress; try again when it finishes."

            def work():
                with run_lock, busy_lease(coordinator.db, "package updates"):  # never under a running production cycle
                    try:
                        bot.notify(maintenance.apply(maintenance.check()))
                    except Exception as e:
                        bot.notify(f"❌ Updates not applied (rolled back): {html.escape(str(e))[:500]}")

            threading.Thread(target=work, name="apply-updates", daemon=True).start()
            return "🧰 Applying updates on a branch and running the tests. I'll report back."

        def trigger_run(content_format: str = "reel") -> str:
            if run_lock.locked():
                return "A run is already in progress."
            scheduler.add_job(production_run, kwargs={"manual": True, "content_format": content_format})
            return "🎬 Starting a run now. You'll get the review card when it's rendered; nothing posts without your approval."

        def rerender_run(run_id: str) -> str:
            refusal = coordinator.rerender_refusal(run_id)
            if refusal:
                return f"🔁 {html.escape(refusal)}"
            if run_lock.locked():
                return "🔁 A run is already in progress; Re-render this one when it has finished."
            extra = coordinator.remake_args(run_id)
            if extra:  # rejected: a regeneration with the recorded reason, reusing what was paid for
                scheduler.add_job(production_run, kwargs={"manual": True, "extra": extra})
            else:
                scheduler.add_job(production_run, kwargs={"manual": True, "resume": run_id})
            return rerender_started(coordinator.db, run_id)

        bot.next_run_func = next_run
        handler = make_command_handler(coordinator.db, next_run, trigger_run, send_photo=bot.send_photo, send_audio=bot.send_audio, send_message=lambda text, buttons=None: bot.notify(text, buttons=buttons, record_alert=False), delete_message=bot.delete_message,
                                       voice_quota=coordinator.audio_engine.voice_quota,
                                       apply_updates=apply_updates, propose_topics=propose_topics,
                                       run_actions={"pubok": coordinator.confirm_posted,
                                                    "pubno": coordinator.confirm_not_posted,
                                                    "resend": resend_for_review,
                                                    "edit": getattr(coordinator, "edit_copy", None),
                                                    "simpub": coordinator.simulate_publish,
                                                    "rerender": rerender_run,
                                                    "promote": getattr(coordinator, "promote_run", None),
                                                    "postnow": getattr(coordinator, "post_now", None),
                                                    "cancelpost": getattr(coordinator, "cancel_post", None),
                                                    "movepost": getattr(coordinator, "move_post", None),
                                                    "retrypost": getattr(coordinator, "retry_post", None),
                                                    "retryreel": getattr(coordinator, "retry_reel", None)},
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
            bot.notify("🔴 <b>Production Daemon Offline</b> • Scheduled runs stopped.", record_alert=False)
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
