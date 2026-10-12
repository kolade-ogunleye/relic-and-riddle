"""
main.py
=======
Central CLI Orchestrator and Automation Scheduler for the Autonomous Meta Content Creation Pipeline.
Powered by SQLite WAL Persistence, Zero-Trust Ops, Multi-Tier Voice Engine,
Hybrid Motion Hook, 3-Tier Publishing Failover, and Multi-Format Companion Generation.

Commands:
  run       Execute full end-to-end creation and publishing cycle.
  daemon    Run autonomous daily scheduler (APScheduler) at configured run time.
  review    Inspect and manage HITL Telegram / SQLite approval queue.
  topic     Inspect topic bank, show next topic, or view publishing history.
  health    Execute pre-flight system health and asset hygiene checks.
  test      Execute rapid end-to-end integration verification test.

Usage:
  python main.py run [--topic-id ID] [--auto-approve] [--dry-run]
  python main.py daemon [--time 09:00]
  python main.py review list
  python main.py review approve <run_id>
  python main.py topic next
  python main.py health
  python main.py test
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import (
    BASE_DIR,
    OUTPUT_DIR,
    DAILY_RUN_TIME,
    TIMEZONE,
)
from src.db import PipelineDatabase
from src.ops import PreFlightHealthChecker, AssetLifecycleManager, SecretMasker
from src.topic_engine import TopicEngine
from src.script_engine import ScriptEngine
from src.audio_engine import AudioEngine
from src.visual_engine import VisualEngine
from src.video_composer import VideoComposer
from src.multi_format import MultiFormatGenerator
from src.telegram_bot import TelegramApprovalBot
from src.publisher import MetaPublisher


class PipelineCoordinator:
    def __init__(self):
        self.db = PipelineDatabase()
        self.health_checker = PreFlightHealthChecker()
        self.lifecycle_manager = AssetLifecycleManager()
        self.masker = SecretMasker()

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
    ) -> dict:
        """
        Executes a complete 10-step enterprise production cycle.
        """
        run_ts = int(time.time())
        print("=" * 75)
        print(f"🎬 [START PIPELINE CYCLE] Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 75)

        # -------------------------------------------------------------
        # STEP 0: Pre-Flight Health & Storage Hygiene Check
        # -------------------------------------------------------------
        print("\n[STEP 0/10] Executing Pre-Flight Health & Storage Hygiene Check...")
        health = self.health_checker.run_all()
        for k, v in health["details"].items():
            icon = "✓" if v["passed"] else "✗"
            print(f"  [{icon}] {k.upper()}: {v['msg']}")

        if not health["all_passed"]:
            raise SystemError("Pre-flight health check failed. Halting pipeline to prevent failure.")

        # Prune expired intermediate scratch assets (> 7 days)
        self.lifecycle_manager.purge_intermediate_assets()

        # -------------------------------------------------------------
        # STEP 1: Topic Selection (SQLite WAL)
        # -------------------------------------------------------------
        if topic_id:
            topic = self.topic_engine.get_topic_by_id(topic_id)
            if not topic:
                raise ValueError(f"Topic ID '{topic_id}' not found in topic bank.")
        else:
            topic = self.topic_engine.get_next_topic()

        tid = topic["id"]
        run_id = f"run_{tid}_{run_ts}"
        run_dir = OUTPUT_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n[STEP 1/10] Selected Topic: '{topic['title']}' ({tid})")
        self.topic_engine.record_status(tid, "in_production", {"run_id": run_id})

        # -------------------------------------------------------------
        # STEP 2: Script Generation (4-Beat Circular Loop)
        # -------------------------------------------------------------
        print(f"\n[STEP 2/10] Generating 4-Beat Circular Documentary Script...")
        script_payload = self.script_engine.generate_script(topic)
        script_payload["topic_id"] = tid

        # -------------------------------------------------------------
        # STEP 3: Stage 2.5 Automated Claim Verification Guardrail
        # -------------------------------------------------------------
        print(f"\n[STEP 3/10] Running Search-Grounded Claim Verification Guardrail...")
        passed, clean_payload, audit_notes = self.script_engine.verify_claims(script_payload, topic=topic)
        print(f"  Guardrail Status: {'PASSED' if passed else 'FAILED'}")
        print(f"  Audit Notes:     {audit_notes}")
        if not passed:
            self.topic_engine.record_status(tid, "rejected_guardrail", {"notes": audit_notes})
            raise RuntimeError(f"Script rejected by Stage 2.5 Guardrail: {audit_notes}")

        # Save script metadata
        script_file = run_dir / "script_metadata.json"
        with open(script_file, "w", encoding="utf-8") as f:
            json.dump(clean_payload, f, indent=2)

        # -------------------------------------------------------------
        # STEP 4: Voiceover Synthesis (Multi-Tier Dispatcher)
        # -------------------------------------------------------------
        print(f"\n[STEP 4/10] Synthesizing Voiceover (Multi-Tier Engine)...")
        vo_path = run_dir / "voiceover.mp3"
        words = self.audio_engine.generate_narration(clean_payload["narration_script"], vo_path)
        vo_dur = self.audio_engine.get_duration(vo_path)
        print(f"  Voiceover Duration: {vo_dur:.2f}s ({len(words)} words mapped)")

        # -------------------------------------------------------------
        # STEP 5: Subterranean Ambient Drone Bed & Diegetic SFX
        # -------------------------------------------------------------
        print(f"\n[STEP 5/10] Synthesizing Subterranean Ambient Bed & Diegetic SFX...")
        bed_path = run_dir / "ambient_bed.wav"
        self.audio_engine.generate_ambient_bed(vo_dur + 0.5, bed_path, words=words)

        # -------------------------------------------------------------
        # STEP 6: Visual Sequencing & Multi-Format Generation
        # -------------------------------------------------------------
        print(f"\n[STEP 6/10] Visual Sequencing & Multi-Format Companion Generation...")
        scene_images = self.visual_engine.get_scene_sequence(topic)
        print(f"  Loaded {len(scene_images)} cinematic scene elements.")

        hero_img = scene_images[0]
        thumb_916, thumb_1x1 = self.visual_engine.generate_thumbnails(hero_img, clean_payload, run_dir)

        # Companion Content: 3-Frame Story Teasers & 7-Slide Carousel
        print(f"  Generating companion 3-Frame Stories & 7-Slide Carousel...")
        story_slides = self.multiformat_gen.generate_story_sequence(hero_img, clean_payload, run_dir)
        carousel_slides = self.multiformat_gen.generate_carousel_sequence(scene_images, clean_payload, run_dir)

        # -------------------------------------------------------------
        # STEP 7: Master Video Assembly (-14 LUFS + Anton 92pt Captions)
        # -------------------------------------------------------------
        print(f"\n[STEP 7/10] Rendering Master Production Video Cut (-14 LUFS Broadcast DSP)...")
        master_mp4 = run_dir / f"{tid}_master.mp4"
        self.video_composer.compose_video(
            scene_images=scene_images,
            vo_path=vo_path,
            bed_path=bed_path,
            words=words,
            vo_dur=vo_dur,
            output_mp4_path=master_mp4,
        )

        # Record run in SQLite WAL
        self.db.record_run(
            run_id=run_id,
            topic_id=tid,
            status="pending_approval",
            script_payload=clean_payload,
            master_video_path=str(master_mp4),
            thumbnail_cover_path=str(thumb_916),
            thumbnail_grid_path=str(thumb_1x1),
        )

        # -------------------------------------------------------------
        # STEP 8: Telegram HITL Notification Dispatch
        # -------------------------------------------------------------
        print(f"\n[STEP 8/10] Dispatching HITL Approval Card to Telegram...")
        msg_id = self.telegram_bot.send_approval_request(
            video_path=master_mp4,
            thumbnail_path=thumb_916,
            script_payload=clean_payload,
            run_id=run_id,
        )

        # -------------------------------------------------------------
        # STEP 9: Await Human Decision (or Auto-Approve)
        # -------------------------------------------------------------
        print(f"\n[STEP 9/10] Awaiting Approval Decision...")
        if auto_approve:
            print("  [AUTO-APPROVE] Flag detected. Automatically approving run.")
            decision = "approved"
        else:
            decision = self.telegram_bot.wait_for_decision(
                run_id=run_id,
                timeout_seconds=300,
                auto_approve_if_unconfigured=True,
            )

        print(f"  Decision Outcome: {decision.upper()}")
        self.db.update_approval_decision(run_id, decision)

        # -------------------------------------------------------------
        # STEP 10: Resilient 3-Tier Publication Dispatch
        # -------------------------------------------------------------
        if decision == "approved":
            print(f"\n[STEP 10/10] Dispatching Meta Publication Envelope (3-Tier Failover)...")
            envelope = self.publisher.build_envelope(
                video_path=master_mp4,
                thumbnail_916_path=thumb_916,
                thumbnail_1x1_path=thumb_1x1,
                script_payload=clean_payload,
            )
            pub_res = self.publisher.publish_reel(envelope, dry_run=dry_run)
            self.topic_engine.record_status(tid, "published", {"run_id": run_id, "publish_res": pub_res})
            print("\n🎉 [CYCLE COMPLETE] Video successfully published to Facebook & Instagram!")
            return {
                "status": "published",
                "run_id": run_id,
                "topic_id": tid,
                "master_video": str(master_mp4),
                "thumbnails": [str(thumb_916), str(thumb_1x1)],
                "stories": [str(s) for s in story_slides],
                "carousel": [str(c) for c in carousel_slides],
                "publish_result": pub_res,
            }
        else:
            print(f"\n⚠️ [CYCLE STOPPED] Decision was '{decision}'. Publishing skipped.")
            self.topic_engine.record_status(tid, f"rejected_{decision}", {"run_id": run_id})
            return {
                "status": decision,
                "run_id": run_id,
                "topic_id": tid,
                "master_video": str(master_mp4),
            }


def main():
    parser = argparse.ArgumentParser(description="Autonomous Meta Reels Content & Publishing Pipeline")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # run command
    run_parser = subparsers.add_parser("run", help="Execute single content production cycle")
    run_parser.add_argument("--topic-id", type=str, default=None, help="Specific topic ID to produce")
    run_parser.add_argument("--auto-approve", action="store_true", help="Automatically approve without waiting")
    run_parser.add_argument("--dry-run", action="store_true", help="Simulate upload dispatch without live API call")

    # daemon command
    daemon_parser = subparsers.add_parser("daemon", help="Run automated daily scheduler")
    daemon_parser.add_argument("--time", type=str, default=DAILY_RUN_TIME, help="Daily run time (HH:MM)")

    # review command
    review_parser = subparsers.add_parser("review", help="Inspect and handle HITL approvals")
    review_sub = review_parser.add_subparsers(dest="review_action")
    review_sub.add_parser("list", help="List pending approvals")
    appr_p = review_sub.add_parser("approve", help="Approve a run")
    appr_p.add_argument("run_id", type=str, help="Run ID to approve")
    rej_p = review_sub.add_parser("reject", help="Reject a run")
    rej_p.add_argument("run_id", type=str, help="Run ID to reject")

    # topic command
    topic_parser = subparsers.add_parser("topic", help="Inspect topic bank and rotation")
    topic_sub = topic_parser.add_subparsers(dest="topic_action")
    topic_sub.add_parser("next", help="Display next scheduled topic")
    topic_sub.add_parser("list", help="List all topics and status")

    # health command
    subparsers.add_parser("health", help="Execute pre-flight system health and storage hygiene checks")

    # test command
    subparsers.add_parser("test", help="Execute rapid end-to-end integration verification test")

    args = parser.parse_args()
    coordinator = PipelineCoordinator()

    if args.command == "run":
        coordinator.run_cycle(
            topic_id=args.topic_id,
            auto_approve=args.auto_approve,
            dry_run=args.dry_run,
        )

    elif args.command == "daemon":
        hour, minute = args.time.split(":")
        scheduler = BlockingScheduler()
        print(f"[DAEMON] Initializing Daily Content Daemon...")
        print(f"  Schedule: Every day at {args.time} ({TIMEZONE})")
        print("  Press Ctrl+C to stop.")

        def scheduled_job():
            print(f"\n[DAEMON] Triggering scheduled daily production run...")
            coordinator.run_cycle(auto_approve=False, dry_run=False)

        scheduler.add_job(
            scheduled_job,
            CronTrigger(hour=int(hour), minute=int(minute), timezone=TIMEZONE),
        )
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            print("[DAEMON] Daemon stopped cleanly.")

    elif args.command == "review":
        db = PipelineDatabase()
        if args.review_action == "list":
            pending = db.list_pending_approvals()
            print(f"\n--- PENDING HITL APPROVALS ({len(pending)}) ---")
            for item in pending:
                script_meta = item.get("script_payload", {})
                print(f"  • Run ID: {item['run_id']} | Topic: {script_meta.get('title', item['topic_id'])} | Date: {item['created_at']}")
        elif args.review_action == "approve":
            bot = TelegramApprovalBot(db=db)
            bot.record_local_decision(args.run_id, "approved")
            print(f"Run '{args.run_id}' approved! Re-run pipeline to complete publish.")
        elif args.review_action == "reject":
            bot = TelegramApprovalBot(db=db)
            bot.record_local_decision(args.run_id, "rejected")
            print(f"Run '{args.run_id}' rejected.")

    elif args.command == "topic":
        te = TopicEngine(db=PipelineDatabase())
        if args.topic_action == "next":
            next_t = te.get_next_topic()
            print(f"\nNext Topic: {next_t['title']} (ID: {next_t['id']})")
            print(f"Hook: {next_t['hook']}")
            print(f"Location: {next_t['location']['name']}, {next_t['location']['country']}")
        elif args.topic_action == "list":
            topics = te.load_bank()
            published = te.get_published_ids()
            print(f"\n--- TOPIC BANK ({len(topics)} Topics, {len(published)} Published) ---")
            for t in topics:
                status = "PUBLISHED" if t["id"] in published else "QUEUED"
                print(f"  [{status:<9}] {t['id']}: {t['title']}")

    elif args.command == "health":
        checker = PreFlightHealthChecker()
        res = checker.run_all()
        print("\nPre-Flight Health Check Results:")
        for k, v in res["details"].items():
            icon = "✓" if v["passed"] else "✗"
            print(f"  [{icon}] {k.upper()}: {v['msg']}")
        print(f"Overall Health: {'READY' if res['all_passed'] else 'ATTENTION REQUIRED'}")

    elif args.command == "test":
        print("\n🧪 [SYSTEM INTEGRATION TEST] Running rapid end-to-end verification...")
        res = coordinator.run_cycle(
            topic_id="01_derinkuyu_underground_city",
            auto_approve=True,
            dry_run=True,
        )
        print("\n" + "=" * 75)
        print("✅ INTEGRATION TEST PASSED SUCCESSFULLY!")
        print(f"Master Video Cut:   {res.get('master_video')}")
        print(f"Locked Thumbnails:  {res.get('thumbnails')}")
        print(f"Stories Generated:  {len(res.get('stories', []))} slides")
        print(f"Carousel Generated: {len(res.get('carousel', []))} slides")
        print("=" * 75)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
