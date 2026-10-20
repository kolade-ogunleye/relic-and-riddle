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
import html
import json
import re
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
from src.control import heartbeat_text, is_paused, make_command_handler, ping_healthcheck
from src import beat_images, callouts, config, encoding, footage, graphics, motion, post_schedule, privacy, then_now
from src.llm import claude_available as llm_available
from src.scene_planner import documentary_cuts, plan_scenes, segment_beats
from src.upscale import ensure_all, ensure_resolution
from src.music import music_for
from src.parallax import render_scenes as render_parallax
from src.video_composer import TRANS_DUR
from src.video_qa import QualityAuditor, qa_summary_html, review_render
from src.visual_engine import COVER_STYLES
from src.ops import AssetLifecycleManager, PreFlightHealthChecker, setup_logging
from src.fact_sheet import FactStarvedTopicError
from src.stage_timer import StageTimer

log = logging.getLogger("pipeline")

MAX_REGENERATIONS = 2
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
        self.paid_voice_in_dry_run = False  # `--paid-voice`: this dry run may use ElevenLabs
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
        health = self.health_checker.run_all(require_live=require_live)
        for k, v in health["details"].items():
            log.info("Health %-11s %s %s", k, "OK " if v["passed"] else ("FAIL" if v["required"] else "warn"), v["msg"])
        if not health["all_passed"]:
            raise SystemExit("Pre-flight health check failed; not starting a run.")
        self.lifecycle_manager.purge_intermediate_assets()

        if topic_id:
            topic = self.topic_engine.get_topic_by_id(topic_id)
            if not topic:
                raise ValueError(f"Topic ID '{topic_id}' not found in topic bank.")
        else:
            topic = self.topic_engine.get_next_topic()
        tid = topic["id"]
        previous_status = topic["status"] if topic["status"] != "in_production" else "failed"

        if content_format == "deep-dive":
            return self.produce_deep_dive(topic, parent_run_id=None, auto_approve=auto_approve, dry_run=dry_run, run_id=run_id)

        run_id = run_id or (f"run_{tid}_{int(time.time())}" + ("_short" if content_format == "dual" else ""))
        run_dir = OUTPUT_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self.db.create_run(run_id, tid, content_format="reel")
        self.topic_engine.record_status(tid, "in_production", run_id=run_id)
        log.info("Run %s: topic '%s'", run_id, topic["title"])

        try:
            result = self._produce_and_publish(topic, run_id, run_dir, auto_approve, dry_run, previous_status)
        except FactStarvedTopicError as e:
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
            if getattr(self, "progress_msg_id", None):
                try:
                    self.telegram_bot.finish_progress_card(self.progress_msg_id, topic["title"], run_id, success=False, error=str(e))
                except Exception:
                    pass
                self.progress_msg_id = None
            self.db.update_run(run_id, "failed", error=f"{type(e).__name__}: {e}")
            self.topic_engine.record_status(tid, "failed", run_id=run_id)
            log.error("Run %s failed: %s", run_id, e)
            self._alert(f"❌ <b><u>PRODUCTION FAILED</u></b>\n\n<b>{html.escape(self.db.run_label(run_id))}</b>\n\n{html.escape(str(e))[:600]}\n\nNothing was posted. The topic will be retried on a later run.")
            ping_healthcheck("/fail")
            raise

        if result["status"] == "regenerate":
            if _regenerations < MAX_REGENERATIONS:
                log.info("Regeneration requested; starting a fresh run for '%s'.", tid)
                return self.run_cycle(tid, auto_approve, dry_run, require_live, content_format, _regenerations + 1)
            log.warning("Regeneration limit reached for '%s'.", tid)
            self.topic_engine.record_status(tid, "failed", run_id=run_id)

        if content_format == "dual":
            log.info("Dual-format requested; producing child 16:9 widescreen documentary...")
            try:
                deep_res = self.produce_deep_dive(topic, parent_run_id=run_id, auto_approve=auto_approve, dry_run=dry_run)
                result["deep_dive_run"] = deep_res
                result["dual_format"] = True
            except Exception as e:
                log.error("Dual-format deep dive child run failed: %s", e)
                result["deep_dive_run"] = {"status": "failed", "error": str(e)}

        return result

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
        if not run_id:
            ts = int(time.time())
            run_id = f"run_{tid}_{ts}_long"
        run_dir = OUTPUT_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        if not self.db.get_run(run_id):
            self.db.create_run(run_id, tid, parent_run_id=parent_run_id, content_format="deep_dive")
        log.info("Starting Deep-Dive Documentary run %s for topic '%s'", run_id, topic["title"])

        try:
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
                deep_dive_pkg = self.script_engine.generate_deep_dive(topic)
                passed, act_issues = self.script_engine.verify_deep_dive_act_by_act(deep_dive_pkg, topic)
                if passed:
                    package_path.write_text(deep_dive_pkg.model_dump_json(indent=2), encoding="utf-8")
                    checkpoints.complete("deep_script", script_inputs, [package_path])
            if act_issues:
                log.warning("Act-by-act verification notes: %s", act_issues)

            full_script_text = deep_dive_pkg.full_narration
            raw_narration = " ".join([act.narration_text for act in deep_dive_pkg.acts])

            if not passed:
                raise ValueError("Deep-dive fact verification failed: " + "; ".join(act_issues or []))
            # Cover copy is the fact-checked title only; the eyebrow and tagline assert nothing.
            thumbnail_copy = documentary_cover(deep_dive_pkg.title)

            payload = {
                "title": deep_dive_pkg.title,
                "youtube_title": deep_dive_pkg.youtube_title,
                "hook": deep_dive_pkg.acts[0].narration_text[:120],
                "narration_script": raw_narration,
                "full_act_script": full_script_text,
                "caption": f"{deep_dive_pkg.title}\n\n" + deep_dive_pkg.description,
                "description": deep_dive_pkg.description,
                "tags": deep_dive_pkg.tags,
                "closing_debate_question": deep_dive_pkg.closing_debate_question,
                "first_comment": (
                    f"Archaeological Inquiry: {deep_dive_pkg.closing_debate_question}\n\n"
                    "Share your perspective below—do ancient megastructures reflect peak human ingenuity or existential terror?"
                ),
                "acts": [act.model_dump() for act in deep_dive_pkg.acts],
                "thumbnail": thumbnail_copy,
                "guardrail_passed": bool(passed),
                "verification_issues": act_issues or [],
                "origin": "deep_dive_engine",
                "delivery_profile": "deep_dive_16x9",
            }
            (run_dir / "script_metadata.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
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
                words = self.audio_engine.generate_narration(raw_narration, vo_path, licensed_only=licensed_req,
                                                             allow_paid=allow_paid)
                words_path.write_text(json.dumps(words), encoding="utf-8")
                vo_dur = self.audio_engine.get_duration(vo_path)
                log.info("Deep-Dive Voiceover: %.2fs (%d words timed)", vo_dur, len(words))
                from src.audio_provenance import record_voice
                record_voice(vo_path, self.audio_engine.last_provider, self.audio_engine.last_licensed, raw_narration)
                checkpoints.complete("deep_voice", voice_inputs,
                                     [vo_path, words_path, vo_path.with_suffix(".mp3.rights.json")])

            # Compute precise chapter timestamps for description, metadata, and cinematic act-transition cues
            chapters, chapter_text, cue_timestamps = compute_act_chapters(deep_dive_pkg.acts, words, vo_dur)
            payload["chapters"] = chapters
            payload["description"] = f"{deep_dive_pkg.description}\n\n{chapter_text}"
            payload["caption"] = f"{deep_dive_pkg.title}\n\n{deep_dive_pkg.description}\n\n{chapter_text}"
            (run_dir / "script_metadata.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            self.db.update_run(run_id, "producing", script_payload=payload)

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
                self.audio_engine.generate_ambient_bed(
                    vo_dur + 0.5, bed_path, words=words,
                    music_path=music.get("path"), music_paths=music.get("paths"),
                    cue_timestamps=cue_timestamps
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
                fut_graphics = pool.submit(graphics.make_all, payload, topic, graphics_dir, photo=hero_img, width=1920, height=1080)
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
            log.info("Documentary timeline: %d cuts, sum=%.2fs (target=%.2fs); %s",
                     len(extended_scenes), sum(durations), vo_dur, plan_notes[:120])

            master_mp4 = run_dir / f"{tid}_documentary_16x9.mp4"
            # Thumbnails need only the hero photo and the cover copy, which nothing below changes,
            # so they are drawn while the documentary renders (one worker: the engine keeps per-call state).
            thumb_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="deep-thumbs")
            fut_thumbs = thumb_pool.submit(lambda: (
                self.visual_engine.generate_thumbnails(hero_img, payload, run_dir),
                self.visual_engine.generate_youtube_thumbnail(hero_img, payload, run_dir / "thumbnail_youtube_16x9.jpg")))
            thumb_pool.shutdown(wait=False)
            try:
                return self._render_and_certify_deep_dive(
                    topic, tid, run_id, run_dir, parent_run_id, payload, words, vo_dur, vo_path, bed_path, words_path,
                    extended_scenes, durations, cuts, master_mp4, checkpoints, file_inputs, cached_artifacts,
                    fut_thumbs, auto_approve, dry_run)
            finally:
                concurrent.futures.wait([fut_thumbs])  # never leave a writer running past the run

        except Exception as e:
            log.error("Deep-dive production failed for run %s: %s", run_id, e, exc_info=True)
            self.db.update_run(run_id, "failed", error=f"{type(e).__name__}: {e}")
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
            "status": "simulated" if dry_run else ("approved" if auto_approve else "pending_approval"),
            "master_video": str(master_mp4),
            "thumbnail_youtube": str(thumb_yt),
            "duration_seconds": vo_dur,
            "format": "deep_dive_16x9",
        }

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
        return f"🧪 <b>Simulated Publish Complete</b> for {html.escape(self.db.run_label(run_id))}. Artifacts certified without live broadcast."

    def request_approval(self, run_id: str, dry_run: bool = False) -> Dict[str, Any]:
        """Re-sends the review card for a rendered run whose approval timed out, then acts on the decision."""
        run = self.db.get_run(run_id)
        if not run or run["status"] not in ("timeout", "pending_approval"):
            raise ValueError(f"Run status '{run and run['status']}' has no approval to re-send.")
        appr = self.db.get_approval(run_id)
        if appr and appr["decision"] == "timeout":
            self.db.reopen_approval(run_id)
        elif not appr:
            self.db.record_approval(run_id, run["topic_id"])
        self.db.update_run(run_id, "pending_approval")
        run_dir = OUTPUT_DIR / run_id
        proxy = run_dir / "review_proxy.mp4"
        video = proxy if proxy.exists() else Path(run["master_video_path"])
        self.telegram_bot.send_approval_request(video, Path(run["thumbnail_cover_path"]), run["script_payload"], run_id)
        decision = self.telegram_bot.wait_for_decision(run_id)
        if decision == "approved":
            return {"run_id": run_id, "decision": decision, **self._after_approval(run_id, dry_run=dry_run)}
        status = {"rejected": "rejected", "regenerate": "regenerate"}.get(decision, "timeout")
        self.db.update_run(run_id, status)
        if decision == "rejected":
            self.topic_engine.record_status(run["topic_id"], "rejected", run_id=run_id)
        return {"run_id": run_id, "decision": decision, "status": decision}

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
        topic = self.topic_engine.get_topic_by_id(run["topic_id"])
        previous_status = "published" if topic["times_published"] else "queued"
        self.db.update_run(run_id, "producing")
        self.topic_engine.record_status(topic["id"], "in_production", run_id=run_id)
        log.info("Resuming run %s", run_id)
        try:
            return self._produce_and_publish(topic, run_id, run_dir, auto_approve, dry_run, previous_status)
        except Exception as e:
            self.db.update_run(run_id, "failed", error=f"{type(e).__name__}: {e}")
            self.topic_engine.record_status(topic["id"], "failed", run_id=run_id)
            log.error("Run %s failed: %s", run_id, e)
            self._alert(f"❌ <b><u>PRODUCTION FAILED</u></b>\n\n<b>{html.escape(self.db.run_label(run_id))}</b>\n\n{html.escape(str(e))[:600]}\n\nNothing was posted. The topic will be retried on a later run.")
            ping_healthcheck("/fail")
            raise

    def voice_inputs(self, script: str, dry_run: bool) -> Dict[str, Any]:
        """What a voiceover depends on (its checkpoint inputs): a change to any of these re-voices.
        Real runs need a licensed voice; dry runs don't spend ElevenLabs credits."""
        return {"script": script, "voice": self.audio_engine.voice, "rate": self.audio_engine.rate,
                "pitch": self.audio_engine.pitch, "eleven_voice": self.audio_engine.eleven_voice,
                "eleven_model": config.ELEVENLABS_MODEL,
                "licensed_only": (not dry_run) and config.VOICE_REQUIRE_LICENSED,
                "allow_paid": (not dry_run) or config.ELEVENLABS_IN_DRY_RUNS or self.paid_voice_in_dry_run}

    def _produce_and_publish(self, topic, run_id, run_dir, auto_approve, dry_run, previous_status) -> Dict[str, Any]:
        self.progress_msg_id = None
        try:
            self.progress_msg_id = self.telegram_bot.send_progress_card(run_id, topic["title"])
        except Exception:
            pass

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
        vo_path = run_dir / "voiceover.mp3"
        words_path = run_dir / "words.json"
        voice_path = run_dir / "voice.json"  # which provider made it, and whether it's licensed
        saved_voice = json.loads(voice_path.read_text(encoding="utf-8")) if voice_path.exists() else {}
        # A real run never reuses an unlicensed (test) voiceover. Older runs without voice.json
        # predate this record, so they are re-voiced too.
        voice_inputs = self.voice_inputs(payload["narration_script"], dry_run)
        licensed_req, allow_paid = voice_inputs["licensed_only"], voice_inputs["allow_paid"]
        t0 = time.perf_counter()
        reusable = checkpoints.reusable("voice", voice_inputs) and (dry_run or not config.VOICE_REQUIRE_LICENSED or saved_voice.get("licensed"))
        if reusable:
            words = json.loads(words_path.read_text(encoding="utf-8"))
            log.info("Resuming with the existing voiceover.")
        else:
            words = self.audio_engine.generate_narration(payload["narration_script"], vo_path, licensed_only=licensed_req,
                                                         allow_paid=allow_paid)
            from src.audio_engine import calibrate_pace
            calibrate_pace(self.audio_engine.last_provider,
                           self.audio_engine.eleven_voice if self.audio_engine.last_provider == "elevenlabs" else self.audio_engine.voice,
                           payload["narration_script"], self.audio_engine.get_duration(vo_path))
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
        payload["voice_note"] = f"🎙️ Voice: {v_prov.title()} ({lic_badge})"
        vo_dur = self.audio_engine.get_duration(vo_path)
        timer.record("voiceover", time.perf_counter() - t0)
        log.info("Voiceover: %.2fs, %d words timed.", vo_dur, len(words))
        if getattr(self, "progress_msg_id", None):
            try:
                self.telegram_bot.update_progress_card(self.progress_msg_id, 2, 5, topic["title"], run_id, f"Voiceover mastered ({vo_dur:.1f}s)")
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
        bed_path = run_dir / "ambient_bed.wav"
        t0 = time.perf_counter()
        from src.delivery_profiles import get_delivery_profile
        from src.audio_provenance import record_bed, audio_source_inputs
        from src.music import load_library, MUSIC_DIR
        profile = get_delivery_profile(payload.setdefault("delivery_profile", "reel_9x16"))
        library_tracks = load_library()
        music_path = run_dir / "music_selection.json"
        bed_inputs = {"voice_and_rules": file_inputs([vo_path, words_path, Path("src/audio_engine.py")]),
                      "profile": profile.model_dump(),
                      "library": [{k: v for k, v in t.items() if k != "uses"} for t in library_tracks],
                      "library_files": file_inputs([MUSIC_DIR / t["file"] for t in library_tracks]),
                      "selected_sources": audio_source_inputs(bed_path)}
        if checkpoints.reusable("bed", bed_inputs):
            music = json.loads(music_path.read_text(encoding="utf-8"))
        else:
            music = music_for(payload, vo_dur + 0.5, run_dir, self.audio_engine.voice_quota())
            self.audio_engine.generate_ambient_bed(vo_dur + 0.5, bed_path, words=words,
                                                  music_path=music["path"], sr=profile.audio_sample_rate,
                                                  loop_seamless=profile.loop_seamless)
            if bed_path.is_file():
                record_bed(bed_path, music, sfx=self.audio_engine.last_sfx_sources)
                music_path.write_text(json.dumps(music, default=str, indent=2), encoding="utf-8")
                bed_inputs["selected_sources"] = audio_source_inputs(bed_path)
                checkpoints.complete("bed", bed_inputs, [bed_path, music_path,
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
        for revision in range(MAX_FACT_REVISIONS + 1):
            payload = self.script_engine.generate_script(topic, revision_notes=revision_notes, previous=payload)
            passed, payload, notes = self.script_engine.verify_claims(payload, topic=topic, allow_unreviewed_curated=dry_run)
            log.info("Guardrail (draft %d): %s", revision + 1, notes)
            if passed or payload.get("origin") not in ("claude", "gemini-free"):
                break
            revision_notes = notes
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
                                                  Path("src/typography.py"), Path("src/config.py")]),
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

            fut_beats = pool.submit(beat_images.source, beats, topic, run_dir / "beat_images", plan=query_plan) if query_plan else None
            fut_footage = pool.submit(footage.source, beats, topic, run_dir / "footage", plan=query_plan) if query_plan else None
            fut_graphics = pool.submit(graphics.make_all, payload, topic, run_dir / "graphics", photo=hero_img)
            fut_callouts = pool.submit(callouts.make, payload, topic, scene_media, run_dir / "graphics")
            fut_then_now = pool.submit(then_now.make, topic, run_dir / "graphics")

            thumb_916, thumb_1x1 = fut_thumb.result()
            story_slides = fut_story.result()
            carousel_slides = fut_carousel.result()

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
        motion_memo: Dict[str, Any] = {}
        for attempt in (1, 2):  # a second pass only if the video check finds a picture mismatch
            scenes, durations, plan_notes = plan_scenes(beats, candidates, feedback=feedback)
            log.info("Scene plan: %d beats (%s).", len(beats), plan_notes[:120])
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
            scenes, motion_used = motion.apply(self.db, scenes, run_dir / "motion", memo=motion_memo, topic=topic)
            payload["motion_note"] = (f"🤖 AI motion ({', '.join(motion_used)}) animates {len(motion_used)} real photo(s); "
                                      "the Reel carries the AI label") if motion_used else ""
            # 2.5D parallax clips for the stills (local GPU); falls back to flat camera moves.
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
                             "rules": file_inputs([Path("src/video_composer.py"), Path("src/config.py"),
                                                   Path("src/encoding.py"), Path("src/encoded_audio.py"), Path("src/delivery_profiles.py")]),
                             "fonts": packaging_inputs["fonts"]}
            if not render_checkpoint.reusable("master", render_inputs):
                self.video_composer.compose_video(scenes, vo_path, bed_path, words, vo_dur, master_mp4, durations=durations,
                                                  emphasis=payload.get("emphasis_words"))
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
            feedback = beat_images.picture_issues(qa)
            if not feedback or attempt == 2:
                break
            log.info("Video check found picture mismatches; re-planning scenes once:\n%s", feedback)
        t0 = time.perf_counter()
        review_mp4 = self.video_composer.make_review_proxy(master_mp4, run_dir / "review_proxy.mp4")
        timer.record("review_proxy", time.perf_counter() - t0)
        payload["qa_summary"] = qa_summary_html(qa)
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

        # 8-9. Human approval (never implicit)
        if auto_approve:
            self.db.record_approval(run_id, tid)
            self.db.update_approval_decision(run_id, "approved", decided_by="auto-approve-flag")
            decision = "approved"
        else:
            if getattr(self, "progress_msg_id", None):
                try:
                    self.telegram_bot.finish_progress_card(self.progress_msg_id, topic["title"], run_id, success=True)
                except Exception:
                    pass
                self.progress_msg_id = None
            self.telegram_bot.send_approval_request(review_mp4, thumb_916, payload, run_id)
            decision = self.telegram_bot.wait_for_decision(run_id)

        result: Dict[str, Any] = {
            "run_id": run_id, "topic_id": tid, "decision": decision,
            "master_video": str(master_mp4), "thumbnails": [str(thumb_916), str(thumb_1x1)],
            "stories": [str(s) for s in story_slides], "carousel": [str(c) for c in carousel_slides],
        }
        if decision != "approved":
            run_status, topic_status = {
                "rejected": ("rejected", "rejected"),
                "regenerate": ("regenerate", None),
                "timeout": ("timeout", "failed"),
            }.get(decision, ("failed", "failed"))
            self.db.update_run(run_id, run_status)
            if topic_status:
                self.topic_engine.record_status(tid, topic_status, run_id=run_id)
            log.info("Not publishing: decision was '%s'.", decision)
            if decision == "timeout":
                key = self.db.run_key(run_id)
                self._alert(f"⏰ <b><u>REVIEW TIMEOUT</u></b>\n\nNo decision in time on <b>{html.escape(self.db.run_label(run_id))}</b>.\n\nThe finished video is preserved and ready for review.",
                            [{"text": "📨 Send it again for review", "callback_data": f"resend:{key}"}])
            result["status"] = decision
            return result

        # 10. Publish (or book the posting slot when running as the daemon)
        result.update(self._after_approval(run_id, dry_run=dry_run, previous_status=previous_status))
        return result

    def _after_approval(self, run_id: str, dry_run: bool = False, previous_status: str = "queued") -> Dict[str, Any]:
        if getattr(config, "BACKUP_ENABLED", True):
            try:
                from src.backup import backup_run
                run = self.db.get_run(run_id) or {}
                if run.get("master_video_path"):
                    backup_run(Path(run["master_video_path"]).parent, db_path=getattr(self.db, "db_path", None))
            except Exception as e:
                log.warning("Tier 2 Gold Backup skipped: %s", e)
        if self.schedule_posts and POST_SCHEDULING and not dry_run:
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
        lines = [f"🎬 Reel: {post_schedule.fmt(slot)}"]
        stories = sorted(str(p) for p in (run_dir / "stories").glob("story_*.jpg"))
        if stories:
            when = post_schedule.story_time(slot)
            self.db.add_scheduled_post(run_id, "story", when.astimezone(timezone.utc).isoformat(), stories)
            lines.append(f"📱 {len(stories)} Story frames: {post_schedule.fmt(when)}")
        carousel = sorted(str(p) for p in (run_dir / "carousel").glob("carousel_slide_*.jpg"))
        if carousel:
            self.db.add_scheduled_post(run_id, "carousel", None, carousel, status="waiting")
            lines.append(f"🖼 Carousel: queued for the next carousel slot (from {post_schedule.fmt(post_schedule.next_carousel_slot(slot))})")
        self.db.update_run(run_id, "scheduled")
        self.topic_engine.record_status(run["topic_id"], "scheduled", run_id=run_id)
        self._alert(f"🗓 <b><u>POST SCHEDULED</u></b>\n\n<b>{html.escape(self.db.run_label(run_id))}</b>\n\n" + "\n".join(html.escape(x) for x in lines))
        return {"status": "scheduled", "scheduled_for": slot.isoformat()}

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
            if due > now:
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
        waiting = self.db.list_scheduled_posts(("waiting",))
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
        with publication_lock(self.db.db_path, run_id):
            return self._publish_run_locked(run_id, dry_run, previous_status)

    def _publish_run_locked(self, run_id: str, dry_run: bool = False, previous_status: str = "queued") -> Dict[str, Any]:
        run = self.db.get_run(run_id)
        if not run or not run.get("master_video_path"):
            raise ValueError(f"Run '{run_id}' has no rendered video.")
        if run.get("status") == "published":
            return {"status": "published", "already_published": True}
        if run.get("status") in ("publishing", "publish_unknown"):
            raise ValueError("Publication is in progress or its outcome is unknown; reconcile it before retrying.")
        payload, tid = run["script_payload"], run["topic_id"]
        from src.delivery_profiles import get_delivery_profile
        profile_name = payload.get("delivery_profile") or ("deep_dive" if run.get("content_format") == "deep_dive" else "reel")
        if get_delivery_profile(profile_name).name != "reel_9x16":
            raise ValueError("No uploader is configured for this delivery profile; use the exported artifacts.")
        if not dry_run and payload.get("origin") == "curated" and not payload.get("human_reviewed"):
            raise ValueError("This run used an unreviewed curated script; it can only be published after review.")
        from src.release import require_release
        require_release(run, dry_run=dry_run)
        envelope = self.publisher.build_envelope(
            run_id, Path(run["master_video_path"]), Path(run["thumbnail_cover_path"]),
            Path(run["thumbnail_grid_path"]), payload,
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
        return {"status": status, "publish_result": pub}


def prebuild_candidates(db: PipelineDatabase, n: int) -> list:
    """The next n topics production would pick (queued first) that have approved images."""
    from src.visual_engine import MissingAssetsError, VisualEngine
    out, ve = [], VisualEngine()
    for t in sorted(db.list_all_topics(), key=lambda t: (t["status"] != "queued", t.get("times_published", 0))):
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
    """Returns a callable that researches topic proposals in the background and sends each with Add/Skip buttons."""
    busy = threading.Lock()

    def send_proposals():
        from src import topic_proposer
        if not busy.acquire(blocking=False):
            return
        try:
            proposals = topic_proposer.propose(db)
            if not proposals:
                bot.notify("💡 No new topic proposals this time (none had verified sources or all overlapped).")
            from src import control
            for p in proposals:
                res = bot._api("sendMessage", data={
                    "chat_id": bot.chat_id, "parse_mode": "HTML", "disable_web_page_preview": "true",
                    "text": topic_proposer.proposal_text(p),
                    "reply_markup": json.dumps({"inline_keyboard": [[
                        {"text": "➕ Add", "callback_data": f"tadd:{p.slug}"},
                        {"text": "✖ Skip", "callback_data": f"tskip:{p.slug}"},
                    ]]}),
                })
                if isinstance(res, dict) and res.get("message_id"):
                    control.active_proposal_msg_ids.append(res["message_id"])
            if proposals:
                done_res = bot.notify(
                    f"💡 Showing <b>{len(proposals)}</b> newly researched topic proposal(s):",
                    buttons=[[{"text": "✖ Done", "callback_data": "tdone:"}]],
                    record_alert=False
                )
                if isinstance(done_res, int):
                    control.active_proposal_msg_ids.append(done_res)
        except Exception as e:
            bot.notify(f"💡 Topic proposals failed: {html.escape(str(e))[:300]}")
        finally:
            busy.release()

    def trigger() -> str:
        if busy.locked():
            return "Already researching topic proposals."
        threading.Thread(target=send_proposals, name="topic-proposals", daemon=True).start()
        return "💡 Researching 3 new topic ideas (~2–3 minutes via live academic/encyclopedic search). Each will arrive with Add / Skip buttons."

    trigger.run_now = send_proposals  # for the weekly scheduled job
    return trigger


def _print_health(res: Dict[str, Any]):
    for k, v in res["details"].items():
        icon = "✓" if v["passed"] else ("✗" if v["required"] else "!")
        print(f"  [{icon}] {k.upper()}: {v['msg']}")
    print(f"Overall: {'READY' if res['all_passed'] else 'ATTENTION REQUIRED'}")


def main(argv=None) -> int:
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
    run_p.add_argument("--paid-voice", action="store_true",
                       help="With --dry-run: voice with ElevenLabs anyway (spends credits); real runs always may")
    run_p.add_argument("--schedule-posts", action="store_true",
                       help="Once approved, book the Reel into the next posting slot instead of posting at once")

    resume_p = sub.add_parser("resume", help="Continue a failed run, reusing its approved script and voiceover")
    resume_p.add_argument("run_id")
    resume_p.add_argument("--auto-approve", action="store_true")
    resume_p.add_argument("--dry-run", action="store_true")

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

    review_p = sub.add_parser("review", help="Inspect and decide pending approvals")
    review_sub = review_p.add_subparsers(dest="review_action")
    review_sub.add_parser("list")
    for action in ("approve", "reject", "regenerate"):
        review_sub.add_parser(action).add_argument("run_id")
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

    health_p = sub.add_parser("health", help="Pre-flight checks")
    health_p.add_argument("--live", action="store_true", help="Also require production credentials")
    test_p = sub.add_parser("test", help="End-to-end dry run on the Derinkuyu topic (publishes nothing)")
    test_p.add_argument("--paid-voice", action="store_true", help="Voice this dry run with ElevenLabs (spends credits)")
    backup_p = sub.add_parser("backup", help="Sync completed runs to OneDrive / Cloud archive")
    backup_p.add_argument("--run-id", help="Specific run ID to backup")
    backup_p.add_argument("--all", action="store_true", help="Backup all finished runs in output/")

    args = parser.parse_args(argv)

    if args.command == "run":
        try:
            coordinator = PipelineCoordinator(schedule_posts=True) if args.schedule_posts else PipelineCoordinator()
            coordinator.paid_voice_in_dry_run = args.paid_voice
            if getattr(args, "background_polling", False):
                coordinator.telegram_bot.background_polling = True
            res = coordinator.run_cycle(args.topic_id, args.auto_approve, args.dry_run, require_live=getattr(args, "require_live", False), content_format=args.format, run_id=getattr(args, "run_id", None))
        except Exception:
            return 1  # already logged and recorded on the run
        summary_keys = ["status", "run_id", "master_video", "dual_format"]
        out_dict = {k: res.get(k) for k in summary_keys if k in res}
        if "deep_dive_run" in res:
            out_dict["deep_dive_run"] = {
                k: res["deep_dive_run"].get(k) for k in ("run_id", "status", "master_video", "duration_seconds")
            }
        print(json.dumps(out_dict, indent=2))
        return 0 if res["status"] in ("published", "simulated") else 1

    if args.command == "resume":
        try:
            res = PipelineCoordinator().resume_run(args.run_id, args.auto_approve, args.dry_run)
        except ValueError as e:
            print(f"Refusing: {e}")
            return 1
        except Exception:
            return 1  # already logged and recorded on the run
        print(json.dumps({k: res.get(k) for k in ("status", "run_id", "master_video")}, indent=2))
        return 0 if res["status"] in ("published", "simulated") else 1

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
        if not bot.is_configured():
            print("Telegram is not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")
            return 1
        bot.register_commands()
        run_lock = threading.Lock()
        worker_proc = None

        def resend_for_review(run_id: str) -> str:
            def work():
                try:
                    coordinator.request_approval(run_id)
                except Exception as e:
                    bot.notify(f"⚠️ Couldn't re-send for review: {html.escape(str(e))[:300]}")
            threading.Thread(target=work, name="resend-review", daemon=True).start()
            return f"📨 Sending {html.escape(coordinator.db.run_label(run_id))} for review again…"

        def trigger_run() -> str:
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

            from src.config import BASE_DIR
            # With posting slots on, the worker books the approved Reel like the daemon does.
            worker_proc = subprocess.Popen(
                [sys.executable, "-u", "main.py", "run", "--require-live", "--background-polling"]
                + (["--schedule-posts"] if POST_SCHEDULING else []),
                cwd=str(BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            threading.Thread(target=monitor_worker, args=(worker_proc,), name="worker-monitor", daemon=True).start()
            return f"🎬 Production run launched in isolated worker (PID {worker_proc.pid}). Live progress updates will stream here."

        handler = make_command_handler(
            db, lambda: "on-demand (bot supervisor)",
            trigger_run,
            send_photo=bot.send_photo, send_audio=bot.send_audio, send_message=bot.notify, delete_message=bot.delete_message, voice_quota=coordinator.audio_engine.voice_quota,
            propose_topics=_proposal_trigger(db, bot),
            run_actions={"pubok": coordinator.confirm_posted,
                         "pubno": coordinator.confirm_not_posted,
                         "resend": resend_for_review,
                         "simpub": coordinator.simulate_publish}
        )
        if POST_SCHEDULING:  # what this mode books, it also posts when due
            start_post_ticker(coordinator, bot)
        from src.control import startup_dashboard_card
        card_text, card_buttons = startup_dashboard_card(db, mode="bot", assets_dir=config.ASSETS_DIR)
        bot.notify(card_text, buttons=card_buttons, record_alert=False)
        log.info("Studio Control Center: listening for Telegram commands. Ctrl+C to stop.")
        try:
            while True:
                try:
                    bot.poll_once(handler)
                except KeyboardInterrupt:
                    raise
                except BaseException as e:
                    log.exception("Telegram polling error (will retry): %s", e)
                    time.sleep(10)
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
        bot.register_commands()
        run_lock = threading.Lock()  # one production run at a time (daily job or /run)

        def production_run(manual: bool = False):
            if not manual and is_paused(coordinator.db):
                log.info("Scheduled run skipped: paused.")
                bot.notify("⏸ Scheduled run skipped (paused). Send /resume to re-enable.")
                return
            if not run_lock.acquire(blocking=False):
                log.warning("A run is already in progress; skipping.")
                return
            try:
                result = coordinator.run_cycle(require_live=True)
                if result.get("status") in ("published", "simulated", "rejected"):
                    ping_healthcheck()
            except QueueEmpty:
                ping_healthcheck()  # the system is healthy; it just has nothing new to make
                bot.notify("📭 <b>Topic queue is empty</b>: every topic has been published, so today's run was skipped "
                           "(repeating topics risks Meta's unoriginal-content rules).",
                           [{"text": "💡 Propose new topics", "callback_data": "propose:"}])
            except BaseException as e:  # keep the daemon alive; the run is already recorded and alerted
                log.exception("Run failed: %s", e)
            finally:
                run_lock.release()

        scheduler = BlockingScheduler(timezone=TIMEZONE)
        daily = scheduler.add_job(production_run, CronTrigger(hour=hour, minute=minute, timezone=TIMEZONE),
                                  max_instances=1, coalesce=True, misfire_grace_time=3600)

        def next_run() -> Optional[str]:
            when = getattr(daily, "next_run_time", None)  # set once the scheduler is running
            return when.strftime("%a %d %b %H:%M %Z") if when else None

        def heartbeat():
            bot.notify(heartbeat_text(coordinator.db, next_run(), coordinator.audio_engine.voice_quota()), silent=True)
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

        if POST_SCHEDULING:  # approved Reels are booked into slots; this publishes them when due
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
                bot.notify(maintenance.report_text(plan))

        scheduler.add_job(weekly_update_check, CronTrigger(day_of_week="mon", hour=hb_hour, minute=hb_minute + 5 if hb_minute < 55 else 0,
                                                           timezone=TIMEZONE), max_instances=1, coalesce=True, misfire_grace_time=3600)

        from src import policy_watch

        def weekly_policy_watch():
            try:
                bot.notify(policy_watch.digest_text(policy_watch.check(coordinator.db)))
            except Exception as e:
                bot.notify(f"📜 Weekly policy watch failed: {html.escape(str(e))[:300]}")

        scheduler.add_job(weekly_policy_watch, CronTrigger(day_of_week="wed", hour=hb_hour, minute=hb_minute,
                                                           timezone=TIMEZONE), max_instances=1, coalesce=True,
                          misfire_grace_time=3600)

        def resend_for_review(run_id: str) -> str:
            """Re-sends a timed-out Reel's review card (in the background; the wait can be long)."""
            def work():
                try:
                    coordinator.request_approval(run_id)
                except Exception as e:
                    bot.notify(f"⚠️ Couldn't re-send for review: {html.escape(str(e))[:300]}")
            threading.Thread(target=work, name="resend-review", daemon=True).start()
            return f"📨 Sending {html.escape(coordinator.db.run_label(run_id))} for review again…"

        propose_topics = _proposal_trigger(coordinator.db, bot)
        def prebuild_fact_sheets():
            """Nightly: verified fact sheets for the next queued topics, so research (and any trouble with
            it) happens well before the daily run, not at 9:00."""
            from src import fact_sheet
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

        def apply_updates() -> str:
            if run_lock.locked():
                return "A production run is in progress; try again when it finishes."

            def work():
                with run_lock:  # never update packages under a running production cycle
                    try:
                        bot.notify(maintenance.apply(maintenance.check()))
                    except Exception as e:
                        bot.notify(f"❌ Updates not applied (rolled back): {html.escape(str(e))[:500]}")

            threading.Thread(target=work, name="apply-updates", daemon=True).start()
            return "🧰 Applying updates on a branch and running the tests. I'll report back."

        def trigger_run() -> str:
            if run_lock.locked():
                return "A run is already in progress."
            scheduler.add_job(production_run, kwargs={"manual": True})
            return "🎬 Starting a run now. You'll get the review card when it's rendered; nothing posts without your approval."

        handler = make_command_handler(coordinator.db, next_run, trigger_run, send_photo=bot.send_photo, send_audio=bot.send_audio, send_message=bot.notify, delete_message=bot.delete_message,
                                       voice_quota=coordinator.audio_engine.voice_quota,
                                       apply_updates=apply_updates, propose_topics=propose_topics,
                                       run_actions={"pubok": coordinator.confirm_posted,
                                                    "pubno": coordinator.confirm_not_posted,
                                                    "resend": resend_for_review,
                                                    "simpub": coordinator.simulate_publish})

        def poll_telegram():
            while True:
                try:
                    bot.poll_once(handler)
                except KeyboardInterrupt:
                    raise
                except BaseException as e:
                    log.exception("Telegram polling error (will retry): %s", e)
                    time.sleep(10)

        if bot.is_configured():
            bot.background_polling = True  # this thread owns getUpdates; approval waits read the database
            threading.Thread(target=poll_telegram, name="telegram-poller", daemon=True).start()

        log.info("Daemon scheduled daily at %s (%s); heartbeat at %s. Ctrl+C to stop.", args.time, TIMEZONE, HEARTBEAT_TIME)
        try:
            from src.control import startup_dashboard_card
            card_text, card_buttons = startup_dashboard_card(coordinator.db, mode="daemon", time_str=args.time, timezone_str=TIMEZONE, assets_dir=config.ASSETS_DIR)
            scheduler.add_job(lambda: bot.notify(card_text, buttons=card_buttons, record_alert=False))
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
            res = coordinator.run_cycle("derinkuyu_underground_city", auto_approve=True, dry_run=True)
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
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
