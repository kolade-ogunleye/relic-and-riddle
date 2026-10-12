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
import html
import json
import re
import threading
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from src.config import ASSETS_DIR, DAILY_RUN_TIME, HARD_DURATION_CEILING, OUTPUT_DIR, TIMEZONE
from src.credits import caption_credit_line, load_credits
from src.db import PipelineDatabase
from src.control import heartbeat_text, is_paused, make_command_handler, ping_healthcheck
from src.scene_planner import plan_scenes, segment_beats
from src.video_qa import qa_summary_html, review_render
from src.ops import AssetLifecycleManager, PreFlightHealthChecker, setup_logging

log = logging.getLogger("pipeline")

MAX_REGENERATIONS = 2
MAX_FACT_REVISIONS = 2  # rewrites of a Claude script that failed fact-checking, before failing closed


class PipelineCoordinator:
    def __init__(self, db: Optional[PipelineDatabase] = None):
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

    def run_cycle(self, topic_id: Optional[str] = None, auto_approve: bool = False, dry_run: bool = False,
                  require_live: bool = False, _regenerations: int = 0) -> Dict[str, Any]:
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

        run_id = f"run_{tid}_{int(time.time())}"
        run_dir = OUTPUT_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self.db.create_run(run_id, tid)
        self.topic_engine.record_status(tid, "in_production", run_id=run_id)
        log.info("Run %s: topic '%s'", run_id, topic["title"])

        try:
            result = self._produce_and_publish(topic, run_id, run_dir, auto_approve, dry_run, previous_status)
        except Exception as e:
            self.db.update_run(run_id, "failed", error=f"{type(e).__name__}: {e}")
            self.topic_engine.record_status(tid, "failed", run_id=run_id)
            log.error("Run %s failed: %s", run_id, e)
            self._alert(f"❌ <b>Run failed</b> <code>{html.escape(run_id)}</code>\n{html.escape(str(e))[:600]}")
            ping_healthcheck("/fail")
            raise

        if result["status"] == "regenerate":
            if _regenerations < MAX_REGENERATIONS:
                log.info("Regeneration requested; starting a fresh run for '%s'.", tid)
                return self.run_cycle(tid, auto_approve, dry_run, require_live, _regenerations + 1)
            log.warning("Regeneration limit reached for '%s'.", tid)
            self.topic_engine.record_status(tid, "failed", run_id=run_id)
        return result

    def _alert(self, text: str) -> None:
        self.telegram_bot.notify(text)

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
            return {"run_id": run_id, "decision": decision, **self.publish_run(run_id, dry_run=dry_run)}
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
            self._alert(f"❌ <b>Run failed</b> <code>{html.escape(run_id)}</code>\n{html.escape(str(e))[:600]}")
            ping_healthcheck("/fail")
            raise

    def _produce_and_publish(self, topic, run_id, run_dir, auto_approve, dry_run, previous_status) -> Dict[str, Any]:
        tid = topic["id"]

        # 1. Visual assets first: fail before spending on LLM/TTS calls.
        scene_media = self.visual_engine.get_scene_sequence(topic)
        hero_img = self.visual_engine.hero_image(scene_media)
        log.info("Scene media: %d items.", len(scene_media))

        # 2-3. Script + guardrail (fails closed). A resumed run reuses a script that already passed.
        saved_script = run_dir / "script_metadata.json"
        saved = json.loads(saved_script.read_text(encoding="utf-8")) if saved_script.exists() else {}
        if saved.get("guardrail_passed"):
            log.info("Resuming with the script that already passed the guardrail.")
            payload = saved
        else:
            payload = self._write_and_check_script(topic, run_id, run_dir, dry_run)

        # 4-5. Audio (reused when resuming)
        vo_path = run_dir / "voiceover.mp3"
        words_path = run_dir / "words.json"
        if vo_path.exists() and words_path.exists():
            words = json.loads(words_path.read_text(encoding="utf-8"))
            log.info("Resuming with the existing voiceover.")
        else:
            words = self.audio_engine.generate_narration(payload["narration_script"], vo_path)
            words_path.write_text(json.dumps(words), encoding="utf-8")
            if self.audio_engine.fallback_reason:
                self._alert(f"🔁 ElevenLabs failed ({html.escape(self.audio_engine.fallback_reason)}); this run uses the free "
                            "edge-tts voice. Check your ElevenLabs quota if this repeats.")
        vo_dur = self.audio_engine.get_duration(vo_path)
        log.info("Voiceover: %.2fs, %d words timed.", vo_dur, len(words))
        if vo_dur > HARD_DURATION_CEILING:
            raise RuntimeError(f"Voiceover is {vo_dur:.1f}s; hard ceiling is {HARD_DURATION_CEILING}s.")
        bed_path = run_dir / "ambient_bed.wav"
        self.audio_engine.generate_ambient_bed(vo_dur + 0.5, bed_path, words=words)
        return self._render_approve_publish(topic, run_id, run_dir, payload, scene_media, hero_img,
                                            vo_path, bed_path, words, vo_dur, auto_approve, dry_run, previous_status)

    def _write_and_check_script(self, topic, run_id, run_dir, dry_run) -> Dict[str, Any]:
        tid = topic["id"]
        revision_notes, payload = "", None
        for revision in range(MAX_FACT_REVISIONS + 1):
            payload = self.script_engine.generate_script(topic, revision_notes=revision_notes, previous=payload)
            passed, payload, notes = self.script_engine.verify_claims(payload, topic=topic, allow_unreviewed_curated=dry_run)
            log.info("Guardrail (draft %d): %s", revision + 1, notes)
            if passed or payload.get("origin") != "claude":
                break
            revision_notes = notes
        if passed:
            credit = caption_credit_line(load_credits(ASSETS_DIR / tid))
            if credit:  # CC BY images must be attributed where the work is shared
                payload["caption"] = f"{payload['caption']}\n\n{credit}"
                if len(payload["caption"]) > 2200:
                    passed, notes = False, "Caption plus image credits exceeds Instagram's 2,200 characters."
        payload["guardrail_passed"] = passed
        (run_dir / "script_metadata.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        self.db.update_run(run_id, "producing", script_payload=payload)
        if not passed:
            raise RuntimeError(f"Script rejected by guardrail: {notes}")
        return payload

    def _render_approve_publish(self, topic, run_id, run_dir, payload, scene_media, hero_img,
                                vo_path, bed_path, words, vo_dur, auto_approve, dry_run, previous_status):
        tid = topic["id"]
        # 6. Stills
        thumb_916, thumb_1x1 = self.visual_engine.generate_thumbnails(hero_img, payload, run_dir)
        story_slides = self.multiformat_gen.generate_story_sequence(hero_img, payload, run_dir)
        carousel_slides = self.multiformat_gen.generate_carousel_sequence(scene_media, payload, run_dir)

        # 7. Master render
        # Match pictures to narration beats; cuts land on the beat boundaries.
        beats = segment_beats(words, vo_dur)
        scenes, durations, plan_notes = plan_scenes(beats, scene_media)
        log.info("Scene plan: %d beats (%s).", len(beats), plan_notes[:120])
        master_mp4 = run_dir / f"{tid}_master.mp4"
        self.video_composer.compose_video(scenes, vo_path, bed_path, words, vo_dur, master_mp4, durations=durations)
        review_mp4 = self.video_composer.make_review_proxy(master_mp4, run_dir / "review_proxy.mp4")

        # Automated look at the finished video; findings go on the review card.
        qa = review_render(master_mp4, beats, thumb_916, run_dir)
        payload["qa_summary"] = qa_summary_html(qa)
        (run_dir / "qa_report.json").write_text(qa.model_dump_json(indent=2) if qa else "null", encoding="utf-8")
        log.info("%s", re.sub(r"<[^>]+>", "", payload["qa_summary"]).replace("\n", " | "))
        self.db.update_run(run_id, "pending_approval", master_video_path=master_mp4,
                           thumbnail_cover_path=thumb_916, thumbnail_grid_path=thumb_1x1)

        # 8-9. Human approval (never implicit)
        if auto_approve:
            self.db.record_approval(run_id, tid)
            self.db.update_approval_decision(run_id, "approved", decided_by="auto-approve-flag")
            decision = "approved"
        else:
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
                self._alert(f"⏰ <b>Approval timed out</b> for <code>{html.escape(run_id)}</code>. The video is kept; "
                            f"re-send it with <code>python main.py review send {html.escape(run_id)}</code>")
            result["status"] = decision
            return result

        # 10. Publish
        result.update(self.publish_run(run_id, dry_run=dry_run, previous_status=previous_status))
        return result

    def publish_run(self, run_id: str, dry_run: bool = False, previous_status: str = "queued") -> Dict[str, Any]:
        run = self.db.get_run(run_id)
        if not run or not run.get("master_video_path"):
            raise ValueError(f"Run '{run_id}' has no rendered video.")
        payload, tid = run["script_payload"], run["topic_id"]
        if not dry_run and payload.get("origin") == "curated" and not payload.get("human_reviewed"):
            raise ValueError("This run used an unreviewed curated script; it can only be published after review.")
        self.db.update_run(run_id, "approved")
        envelope = self.publisher.build_envelope(
            run_id, Path(run["master_video_path"]), Path(run["thumbnail_cover_path"]),
            Path(run["thumbnail_grid_path"]), payload,
        )
        pub = self.publisher.publish_reel(envelope, dry_run=dry_run)
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
            self._alert(f"✅ <b>Published</b> {html.escape(payload.get('title', tid))}\n{links}{warn}")
        elif status == "simulated":
            self.db.update_run(run_id, "simulated")
            self.topic_engine.record_status(tid, previous_status, run_id=run_id)
        elif status == "unknown":
            self.db.update_run(run_id, "publish_unknown", error=pub.get("error"))
            self.topic_engine.record_status(tid, "publish_unknown", run_id=run_id)
            log.error("Publish outcome UNKNOWN for %s. Check the accounts, then `topic requeue %s` if it did not post.", run_id, tid)
            self._alert(f"⚠️ <b>Publish outcome unknown</b> for <code>{html.escape(run_id)}</code>. Check Facebook/Instagram "
                        f"before retrying; if it did not post: <code>python main.py topic requeue {html.escape(tid)}</code>")
        else:
            self.db.update_run(run_id, "failed", error=pub.get("error"))
            self.topic_engine.record_status(tid, "failed", run_id=run_id)
            log.error("Publishing failed for %s: %s", run_id, pub.get("error"))
            self._alert(f"❌ <b>Publishing failed</b> for <code>{html.escape(run_id)}</code>: {html.escape(str(pub.get('error')))[:500]}")
        return {"status": status, "publish_result": pub}


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
            for p in proposals:
                bot._api("sendMessage", data={
                    "chat_id": bot.chat_id, "parse_mode": "HTML", "disable_web_page_preview": "true",
                    "text": topic_proposer.proposal_text(p),
                    "reply_markup": json.dumps({"inline_keyboard": [[
                        {"text": "➕ Add", "callback_data": f"tadd:{p.slug}"},
                        {"text": "✖ Skip", "callback_data": f"tskip:{p.slug}"},
                    ]]}),
                })
        except Exception as e:
            bot.notify(f"💡 Topic proposals failed: {html.escape(str(e))[:300]}")
        finally:
            busy.release()

    def trigger() -> str:
        if busy.locked():
            return "Already researching topic proposals."
        threading.Thread(target=send_proposals, name="topic-proposals", daemon=True).start()
        return "💡 Researching 3 new topic ideas (about a minute). Each will arrive with Add / Skip buttons."

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

    resume_p = sub.add_parser("resume", help="Continue a failed run, reusing its approved script and voiceover")
    resume_p.add_argument("run_id")
    resume_p.add_argument("--auto-approve", action="store_true")
    resume_p.add_argument("--dry-run", action="store_true")

    sub.add_parser("bot", help="Run only the Telegram bot (image review, approvals, /status); no scheduled runs")
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
    sub.add_parser("test", help="End-to-end dry run on the Derinkuyu topic (publishes nothing)")

    args = parser.parse_args(argv)

    if args.command == "run":
        try:
            res = PipelineCoordinator().run_cycle(args.topic_id, args.auto_approve, args.dry_run)
        except Exception:
            return 1  # already logged and recorded on the run
        print(json.dumps({k: res.get(k) for k in ("status", "run_id", "master_video")}, indent=2))
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

    if args.command == "bot":
        db = PipelineDatabase()
        from src.audio_engine import AudioEngine
        from src.telegram_bot import TelegramApprovalBot
        bot = TelegramApprovalBot(db=db)
        if not bot.is_configured():
            print("Telegram is not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")
            return 1
        handler = make_command_handler(
            db, lambda: "none (bot-only mode)",
            lambda: "Production runs are disabled in bot-only mode. Use the daemon or `python main.py run`.",
            send_photo=bot.send_photo, voice_quota=AudioEngine().voice_quota,
            propose_topics=_proposal_trigger(db, bot),
        )
        bot.notify("🤖 <b>Bot-only mode</b>: image review, approvals and /status are live; no runs are scheduled. "
                   "Send /images to start.")
        log.info("Bot-only mode: listening for Telegram commands. Ctrl+C to stop.")
        try:
            while True:
                try:
                    bot.poll_once(handler)
                except Exception as e:
                    log.warning("Telegram polling error (will retry): %s", e)
                    time.sleep(10)
        except KeyboardInterrupt:
            bot.notify("🤖 Bot-only mode stopped.")
        return 0

    if args.command == "daemon":
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger

        from src.config import HEARTBEAT_TIME

        hour, minute = (int(x) for x in args.time.split(":"))
        hb_hour, hb_minute = (int(x) for x in HEARTBEAT_TIME.split(":"))
        coordinator = PipelineCoordinator()
        bot = coordinator.telegram_bot
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
            bot.notify(heartbeat_text(coordinator.db, next_run(), coordinator.audio_engine.voice_quota()))
            ping_healthcheck()

        scheduler.add_job(heartbeat, CronTrigger(hour=hb_hour, minute=hb_minute, timezone=TIMEZONE),
                          max_instances=1, coalesce=True, misfire_grace_time=3600)

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

        propose_topics = _proposal_trigger(coordinator.db, bot)
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

        handler = make_command_handler(coordinator.db, next_run, trigger_run, send_photo=bot.send_photo,
                                       voice_quota=coordinator.audio_engine.voice_quota,
                                       apply_updates=apply_updates, propose_topics=propose_topics)

        def poll_telegram():
            while True:
                try:
                    bot.poll_once(handler)
                except Exception as e:
                    log.warning("Telegram polling error (will retry): %s", e)
                    time.sleep(10)

        if bot.is_configured():
            bot.background_polling = True  # this thread owns getUpdates; approval waits read the database
            threading.Thread(target=poll_telegram, name="telegram-poller", daemon=True).start()

        log.info("Daemon scheduled daily at %s (%s); heartbeat at %s. Ctrl+C to stop.", args.time, TIMEZONE, HEARTBEAT_TIME)
        try:
            scheduler.add_job(lambda: bot.notify(
                f"🟢 <b>Daemon started</b>. Daily run at {html.escape(args.time)} {html.escape(TIMEZONE)}"
                f"{' (paused)' if is_paused(coordinator.db) else ''}. Send /help for commands."))
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            log.info("Daemon stopped.")
            bot.notify("🔴 <b>Daemon stopped</b>.")
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
            res = PipelineCoordinator().run_cycle("01_derinkuyu_underground_city", auto_approve=True, dry_run=True)
        except Exception:
            return 1
        print(json.dumps({k: res.get(k) for k in ("status", "master_video", "thumbnails")}, indent=2))
        print(f"Stories: {len(res.get('stories', []))}, carousel slides: {len(res.get('carousel', []))}")
        return 0 if res["status"] == "simulated" else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
