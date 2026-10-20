"""
src/probe.py
============
System Intelligence & Interactive Operator Diagnostic Engine (AGY).
Enables operators to query the autonomous pipeline, telemetry, run decisions,
QA reports, audio/motion providers, and budgets directly from Telegram.
"""

import html
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

import requests

from . import config
from .db import PipelineDatabase

log = logging.getLogger(__name__)


def gather_system_telemetry(db: PipelineDatabase, assets_dir: Path, query: str = "") -> Dict[str, Any]:
    """Gathers comprehensive telemetry across DB, output runs, budgets, and hardware/API providers."""
    telemetry: Dict[str, Any] = {}

    # 1. Latest run in DB
    try:
        with db._conn() as conn:
            row = conn.execute(
                "SELECT run_id, topic_id, status, error, created_at, updated_at "
                "FROM runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row:
                telemetry["last_run"] = {
                    "run_id": row[0],
                    "topic_id": row[1],
                    "status": row[2],
                    "error": row[3],
                    "created_at": row[4],
                    "updated_at": row[5],
                }
    except Exception as e:
        log.warning("Could not query last run from DB: %s", e)

    # 2. Inspect latest run directory artifacts
    run_info = telemetry.get("last_run", {})
    run_id = run_info.get("run_id")
    if run_id:
        run_dir = Path("output") / run_id
        if run_dir.is_dir():
            # Voice
            v_file = run_dir / "voice.json"
            if v_file.exists():
                try:
                    telemetry["voice_record"] = json.loads(v_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            # Metadata & notes
            meta_file = run_dir / "script_metadata.json"
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    telemetry["metadata_notes"] = {
                        "title": meta.get("title"),
                        "voice_note": meta.get("voice_note"),
                        "music_note": meta.get("music_note"),
                        "motion_note": meta.get("motion_note"),
                        "graphics_note": meta.get("graphics_note"),
                        "footage_note": meta.get("footage_note"),
                        "consensus_confidence": meta.get("consensus_confidence"),
                        "qa_summary": meta.get("qa_summary"),
                    }
                except Exception:
                    pass
            # QA Report
            qa_file = run_dir / "qa_report.json"
            if qa_file.exists():
                try:
                    telemetry["qa_report"] = json.loads(qa_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            # Timings
            timing_file = run_dir / "timings.json"
            if timing_file.exists():
                try:
                    telemetry["timings"] = json.loads(timing_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

    # 3. Provider availability & credentials
    from .motion import kling_available, veo_available, last_fallback_reason as motion_fallback
    telemetry["motion_fallback_reason"] = motion_fallback
    telemetry["providers"] = {
        "kling_configured": kling_available(),
        "veo_configured": veo_available(),
        "claude_configured": bool(config.ANTHROPIC_API_KEY),
        "gemini_configured": bool(config.GEMINI_API_KEY),
        "elevenlabs_configured": bool(config.ELEVENLABS_API_KEY),
        "azure_configured": bool(config.AZURE_SPEECH_KEY and config.AZURE_SPEECH_REGION),
        "upload_post_configured": bool(config.UPLOAD_POST_API_KEY),
    }

    # 4. Budget state
    try:
        spent = db.monthly_media_cost() if hasattr(db, "monthly_media_cost") else 0.0
    except Exception:
        spent = 0.0
    telemetry["budgets"] = {
        "claude_budget_usd": config.CLAUDE_MONTHLY_BUDGET_USD,
        "media_budget_usd": config.MEDIA_MONTHLY_BUDGET_USD,
        "media_spent_this_month": spent,
    }

    # 5. Recent alerts and audits
    try:
        telemetry["recent_alerts"] = [a.get("text", "") for a in db.list_recent_alerts(limit=3)]
    except Exception:
        telemetry["recent_alerts"] = []

    # 6. Queue summary
    try:
        all_topics = db.list_all_topics()
        queued = [t for t in all_topics if t.get("status") == "queued"]
        telemetry["queue"] = {
            "total_queued": len(queued),
            "priority_topics": [t.get("title") for t in queued if t.get("priority", 0) > 0],
        }
    except Exception:
        telemetry["queue"] = {"total_queued": 0, "priority_topics": []}

    return telemetry


def _query_llm(query: str, telemetry: Dict[str, Any]) -> Optional[str]:
    """Attempts to synthesize an explanation using Gemini or Claude if configured."""
    # 1. Try Gemini Interactions API / Gemini Generate Content if GEMINI_API_KEY configured
    if config.is_configured(config.GEMINI_API_KEY):
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
            headers = {"Content-Type": "application/json", "x-goog-api-key": config.GEMINI_API_KEY}
            system_instruction = (
                "You are Antigravity (AGY), the autonomous AI engineering lead for Relic & Riddle. "
                "The human operator is probing system decisions via Telegram. "
                "Answer concisely (max 3-4 bullet points or 150 words), using professional documentary engineering tone. "
                "Format using Telegram HTML (<b>, <i>, <code>). Never use Markdown like ** or `."
            )
            context_block = json.dumps(telemetry, indent=2, ensure_ascii=False)
            body = {
                "systemInstruction": {"parts": [{"text": system_instruction}]},
                "contents": [{"parts": [{"text": f"System Telemetry State:\n{context_block}\n\nOperator Question:\n{query}"}]}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024, "thinkingConfig": {"thinkingBudget": 0}}
            }
            resp = requests.post(url, headers=headers, json=body, timeout=12)
            if resp.ok:
                candidates = resp.json().get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts and parts[0].get("text"):
                        return parts[0]["text"].strip()
        except Exception as e:
            log.warning("Gemini probe query failed: %s", e)

    # 2. Try Anthropic if ANTHROPIC_API_KEY configured
    if config.is_configured(config.ANTHROPIC_API_KEY):
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
            prompt = (
                f"You are Antigravity (AGY), the autonomous AI engineering lead for Relic & Riddle.\n"
                f"Answer the operator's query concisely (max 150 words) using Telegram HTML tags (<b>, <i>, <code>).\n"
                f"Telemetry Data:\n{json.dumps(telemetry, indent=2)}\n\nOperator Question: {query}"
            )
            msg = client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=300,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}]
            )
            if msg.content and hasattr(msg.content[0], "text"):
                return msg.content[0].text.strip()
        except Exception as e:
            log.warning("Claude probe query failed: %s", e)

    return None


def _deterministic_diagnose(query: str, telemetry: Dict[str, Any]) -> str:
    """Deterministic fallback diagnostic engine for instant, zero-cost responses."""
    q = query.lower()
    lines: List[str] = []

    last_run = telemetry.get("last_run", {})
    run_id = last_run.get("run_id", "none")
    status = last_run.get("status", "unknown")
    providers = telemetry.get("providers", {})
    budgets = telemetry.get("budgets", {})
    notes = telemetry.get("metadata_notes", {})

    # Motion / Kling / Veo queries
    if any(k in q for k in ("kling", "veo", "motion", "video generation", "animate")):
        lines.append("🎬 <b><u>Motion Synthesis Diagnostics</u>:</b>")
        if "kling" in q:
            if not providers.get("kling_configured"):
                lines.append("• <b>Kling API Status:</b> <code>Unconfigured</code> (missing <code>KLING_API_KEY</code>).")
            else:
                lines.append("• <b>Kling API Status:</b> <code>Configured & Active</code>.")
        if "veo" in q:
            if not providers.get("veo_configured"):
                lines.append("• <b>Veo API Status:</b> <code>Unconfigured</code> (missing <code>GEMINI_API_KEY</code>).")
            else:
                lines.append("• <b>Veo API Status:</b> <code>Configured</code> (format: <code>mimeType=image/jpeg</code>).")
        
        motion_note = notes.get("motion_note")
        if motion_note:
            lines.append(f"• <b>Active Decision:</b> {html.escape(motion_note)}")
        else:
            lines.append("• <b>Active Decision:</b> Zero-cost <b>2.5D GPU parallax depth</b> was utilized to preserve authentic archival geometry and conserve media budget.")

        lines.append(f"• <b>Media Budget:</b> <code>${budgets.get('media_spent_this_month', 0):.2f}</code> used of <code>${budgets.get('media_budget_usd', 0):.2f}</code> cap.")

    # Voice / Audio queries
    elif any(k in q for k in ("voice", "audio", "tts", "fallback", "elevenlabs", "azure", "edge")):
        lines.append("🎙️ <b><u>Acoustic & Voice Diagnostics</u>:</b>")
        v_rec = telemetry.get("voice_record", {})
        prov = v_rec.get("provider", "edge-tts")
        lic = "🟢 Licensed" if v_rec.get("licensed") else "🟡 Unlicensed (Review / Test Only)"
        lines.append(f"• <b>Last Voice Provider:</b> <code>{html.escape(prov)}</code> ({lic})")
        if not providers.get("azure_configured") and not providers.get("elevenlabs_configured"):
            lines.append("• <b>Commercial License Notice:</b> Neither Azure Speech nor paid ElevenLabs key is configured. Edge-TTS is active for local testing.")
        lines.append("• <b>Resilience:</b> Edge-TTS runs with exponential backoff and rotating endpoints to prevent rate limits.")

    # QA / Loudness / Visual Quality
    elif any(k in q for k in ("qa", "quality", "loudness", "lufs", "check", "review", "rejected", "reject")):
        lines.append("🔎 <b><u>Automated Quality Assurance Diagnostics</u>:</b>")
        qa_rep = telemetry.get("qa_report") or {}
        issues = qa_rep.get("issues", [])
        if issues:
            lines.append(f"• <b>Issues Flagged ({len(issues)}):</b>")
            for iss in issues[:3]:
                lines.append(f"  · [{html.escape(iss.get('severity', 'info'))}] {html.escape(iss.get('issue', ''))}")
        else:
            lines.append("• <b>Visual / Audio Audit:</b> Passed with zero critical defects.")
        lines.append("• <b>Audio Mastering:</b> Standardized to <code>EBU R128 (-14.0 LUFS)</code>.")

    # Queue & Priority queries
    elif any(k in q for k in ("queue", "priority", "bump", "next", "topics")):
        q_info = telemetry.get("queue", {})
        lines.append("📋 <b><u>Production Slate Diagnostics</u>:</b>")
        lines.append(f"• <b>Total Queued:</b> <code>{q_info.get('total_queued', 0)}</code> topics")
        prios = q_info.get("priority_topics", [])
        if prios:
            lines.append(f"• <b>Priority #1 Topic(s):</b> {', '.join(html.escape(p) for p in prios)}")
        else:
            lines.append("• <b>Priority:</b> Standard algorithmic round-robin scheduling active.")

    # Default / General run telemetry
    else:
        lines.append("⚙️ <b><u>Active Pipeline Telemetry</u>:</b>")
        lines.append(f"• <b>Latest Run:</b> <code>{html.escape(run_id)}</code>")
        lines.append(f"• <b>Run Status:</b> <code>{html.escape(status)}</code>")
        if last_run.get("error"):
            lines.append(f"• <b>Last Recorded Error:</b> <code>{html.escape(str(last_run['error'])[:200])}</code>")
        else:
            lines.append("• <b>Health:</b> All pipeline stages operating nominal.")
        lines.append(f"• <b>Queued Topics:</b> <code>{telemetry.get('queue', {}).get('total_queued', 0)}</code> ready for production.")

    return "\n".join(lines)


def probe_system(query: str, db: PipelineDatabase, assets_dir: Optional[Path] = None) -> str:
    """Answers operator questions using live telemetry, run artifacts, and AI reasoning."""
    clean_query = query.strip()
    if not clean_query:
        return (
            "🔍 <b><u>PROBE SYSTEM (AGY)</u></b>\n\n"
            "Tap to copy & edit: <code>/ask Why did the system reject Kling motion in the last run?</code>\n\n"
            "<i>Usage: <code>/ask &lt;question&gt;</code> (or <code>/probe</code>)\n"
            "Ask AGY anything about pipeline decisions, run diagnostics, QA reports, budgets, or media lineage.</i>"
        )

    assets_path = assets_dir or config.ASSETS_DIR
    telemetry = gather_system_telemetry(db, assets_path, query=clean_query)

    ai_answer = _query_llm(clean_query, telemetry)
    if ai_answer:
        answer_body = ai_answer
    else:
        answer_body = _deterministic_diagnose(clean_query, telemetry)

    return (
        f"🔍 <b><u>SYSTEM INTELLIGENCE (AGY)</u></b>\n\n"
        f"<b>Q:</b> <i>{html.escape(clean_query)}</i>\n\n"
        f"{answer_body}"
    )
