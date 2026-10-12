"""
main.py
=======
CLI orchestrator and daily scheduler for the Autonomous Meta Reels pipeline.

Usage:
  python main.py run [--topic-id ID] [--auto-approve] [--dry-run]
  python main.py daemon [--time 09:00]
  python main.py review list
  python main.py review approve|reject|regenerate <run_id>   (while a run is waiting)
  python main.py review publish <run_id> [--dry-run]         (publish a finished, unpublished run)
  python main.py topic next | list | requeue <topic_id> | repair
  python main.py assets list | approve <topic_id>            (review fetched scene images)
  python main.py health [--live]
  python main.py test                                        (end-to-end dry run, nothing published)
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from src.config import ASSETS_DIR, DAILY_RUN_TIME, HARD_DURATION_CEILING, OUTPUT_DIR, TIMEZONE
from src.credits import caption_credit_line, load_credits
from src.db import PipelineDatabase
from src.ops import AssetLifecycleManager, PreFlightHealthChecker, setup_logging

log = logging.getLogger("pipeline")

MAX_REGENERATIONS = 2


class PipelineCoordinator:
    def __init__(self, db: Optional[PipelineDatabase] = None):
        # Imported here so `health`/`topic`/`review` work even if a media dependency is broken.
        from src.audio_engine import AudioEngine
        from src.multi_format import MultiFormatGenerator
        from src.publisher import MetaPublisher
        from src.script_engine import ScriptEngine
        from src.telegram_bot import TelegramApprovalBot
        from src.topic_engine import TopicEngine
        from src.video_composer import VideoComposer
        from src.visual_engine import VisualEngine

        self.db = db or PipelineDatabase()
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
            raise

        if result["status"] == "regenerate":
            if _regenerations < MAX_REGENERATIONS:
                log.info("Regeneration requested; starting a fresh run for '%s'.", tid)
                return self.run_cycle(tid, auto_approve, dry_run, require_live, _regenerations + 1)
            log.warning("Regeneration limit reached for '%s'.", tid)
            self.topic_engine.record_status(tid, "failed", run_id=run_id)
        return result

    def _produce_and_publish(self, topic, run_id, run_dir, auto_approve, dry_run, previous_status) -> Dict[str, Any]:
        tid = topic["id"]

        # 1. Visual assets first: fail before spending on LLM/TTS calls.
        scene_media = self.visual_engine.get_scene_sequence(topic)
        hero_img = self.visual_engine.hero_image(scene_media)
        log.info("Scene media: %d items.", len(scene_media))

        # 2-3. Script + guardrail (fails closed)
        payload = self.script_engine.generate_script(topic)
        passed, payload, notes = self.script_engine.verify_claims(payload, topic=topic, allow_unreviewed_curated=dry_run)
        log.info("Guardrail: %s", notes)
        if passed:
            credit = caption_credit_line(load_credits(ASSETS_DIR / tid))
            if credit:  # CC BY images must be attributed where the work is shared
                payload["caption"] = f"{payload['caption']}\n\n{credit}"
                if len(payload["caption"]) > 2200:
                    passed, notes = False, "Caption plus image credits exceeds Instagram's 2,200 characters."
        (run_dir / "script_metadata.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        self.db.update_run(run_id, "producing", script_payload=payload)
        if not passed:
            raise RuntimeError(f"Script rejected by guardrail: {notes}")

        # 4-5. Audio
        vo_path = run_dir / "voiceover.mp3"
        words = self.audio_engine.generate_narration(payload["narration_script"], vo_path)
        vo_dur = self.audio_engine.get_duration(vo_path)
        log.info("Voiceover: %.2fs, %d words timed.", vo_dur, len(words))
        if vo_dur > HARD_DURATION_CEILING:
            raise RuntimeError(f"Voiceover is {vo_dur:.1f}s; hard ceiling is {HARD_DURATION_CEILING}s.")
        bed_path = run_dir / "ambient_bed.wav"
        self.audio_engine.generate_ambient_bed(vo_dur + 0.5, bed_path, words=words)

        # 6. Stills
        thumb_916, thumb_1x1 = self.visual_engine.generate_thumbnails(hero_img, payload, run_dir)
        story_slides = self.multiformat_gen.generate_story_sequence(hero_img, payload, run_dir)
        carousel_slides = self.multiformat_gen.generate_carousel_sequence(scene_media, payload, run_dir)

        # 7. Master render
        master_mp4 = run_dir / f"{tid}_master.mp4"
        self.video_composer.compose_video(scene_media, vo_path, bed_path, words, vo_dur, master_mp4)
        review_mp4 = self.video_composer.make_review_proxy(master_mp4, run_dir / "review_proxy.mp4")
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
        elif status == "simulated":
            self.db.update_run(run_id, "simulated")
            self.topic_engine.record_status(tid, previous_status, run_id=run_id)
        elif status == "unknown":
            self.db.update_run(run_id, "publish_unknown", error=pub.get("error"))
            self.topic_engine.record_status(tid, "publish_unknown", run_id=run_id)
            log.error("Publish outcome UNKNOWN for %s. Check the accounts, then `topic requeue %s` if it did not post.", run_id, tid)
        else:
            self.db.update_run(run_id, "failed", error=pub.get("error"))
            self.topic_engine.record_status(tid, "failed", run_id=run_id)
            log.error("Publishing failed for %s: %s", run_id, pub.get("error"))
        return {"status": status, "publish_result": pub}


def _print_health(res: Dict[str, Any]):
    for k, v in res["details"].items():
        icon = "✓" if v["passed"] else ("✗" if v["required"] else "!")
        print(f"  [{icon}] {k.upper()}: {v['msg']}")
    print(f"Overall: {'READY' if res['all_passed'] else 'ATTENTION REQUIRED'}")


def main(argv=None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Autonomous Meta Reels content & publishing pipeline")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Produce (and, once approved, publish) one Reel")
    run_p.add_argument("--topic-id")
    run_p.add_argument("--auto-approve", action="store_true", help="Skip human review (records who approved)")
    run_p.add_argument("--dry-run", action="store_true", help="Do everything except publish")

    daemon_p = sub.add_parser("daemon", help="Run the daily scheduler")
    daemon_p.add_argument("--time", default=DAILY_RUN_TIME, help="Daily run time HH:MM")

    review_p = sub.add_parser("review", help="Inspect and decide pending approvals")
    review_sub = review_p.add_subparsers(dest="review_action")
    review_sub.add_parser("list")
    for action in ("approve", "reject", "regenerate"):
        review_sub.add_parser(action).add_argument("run_id")
    pub_p = review_sub.add_parser("publish", help="Publish a rendered run that was never published")
    pub_p.add_argument("run_id")
    pub_p.add_argument("--dry-run", action="store_true")

    topic_p = sub.add_parser("topic", help="Inspect topic rotation")
    topic_sub = topic_p.add_subparsers(dest="topic_action")
    topic_sub.add_parser("next")
    topic_sub.add_parser("list")
    topic_sub.add_parser("requeue").add_argument("topic_id")
    topic_sub.add_parser("repair", help="Requeue topics wrongly counted as published by simulated runs")

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

    if args.command == "daemon":
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger

        hour, minute = (int(x) for x in args.time.split(":"))
        coordinator = PipelineCoordinator()

        def scheduled_job():
            try:
                coordinator.run_cycle(require_live=True)
            except BaseException as e:  # keep the daemon alive; the run is already recorded as failed
                log.exception("Scheduled run failed: %s", e)

        scheduler = BlockingScheduler(timezone=TIMEZONE)
        scheduler.add_job(scheduled_job, CronTrigger(hour=hour, minute=minute, timezone=TIMEZONE),
                          max_instances=1, coalesce=True, misfire_grace_time=3600)
        log.info("Daemon scheduled daily at %s (%s). Ctrl+C to stop.", args.time, TIMEZONE)
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            log.info("Daemon stopped.")
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
        elif args.topic_action == "repair":
            fixed = db.repair_simulated_publications()
            print(f"Requeued {len(fixed)} topic(s) with no live publication: {fixed}")
        else:
            topic_p.print_help()
        return 0

    if args.command == "assets":
        from src.config import MIN_SCENE_IMAGES
        from src.credits import CONTACT_SHEET, CREDITS_FILE, load_credits, save_credits
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
            topic_dir = ASSETS_DIR / args.topic_id
            if not (topic_dir / CREDITS_FILE).exists():
                print(f"No fetched images for {args.topic_id}.")
                return 1
            credits = load_credits(topic_dir)
            if len(credits["images"]) < MIN_SCENE_IMAGES:
                print(f"Only {len(credits['images'])} images; need {MIN_SCENE_IMAGES}. Re-run the fetcher to refill.")
                return 1
            credits["reviewed"] = True
            save_credits(topic_dir, credits)
            print(f"Approved {len(credits['images'])} images for {args.topic_id} (reviewed via {CONTACT_SHEET}).")
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
