"""Round 1 Visual Mocks (7-Core Suite) for @relicandriddle_dev_bot.

Posts the 7 core operational surfaces to their settled topics:
  1. Studio Dashboard (idle & live) -> Studio (4906)
  2. Needs you (paged backlog + alert line) -> Needs you (4907)
  3. Review Pair (sendVideo + rich card) -> Reviews (4908)
  4. Runs Progress Card -> Runs (4909)
  5. Runs Outcome Line -> Runs (4909)
  6. Analytics Card (with audience chart) -> Studio (4906)
  7. Confirmations & Rejection Reason Chips -> Studio (4906)
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import audience_chart, config  # noqa: E402

TOKEN = config.TELEGRAM_BOT_TOKEN
CHAT = config.TELEGRAM_CHAT_ID
DEV_ID = 8701645452

TOPICS = {
    "studio": 4906,
    "needs": 4907,
    "reviews": 4908,
    "runs": 4909,
    "info": 4910,
}

RUN_DIR = ROOT / "output" / "run_nazca_lines_runway_paradox_1793267092"
PROXY_VIDEO = RUN_DIR / "review_proxy.mp4"
COVER_IMAGE = RUN_DIR / "thumbnail_cover.jpg"
TMP_DIR = Path(r"C:\tmp\tg_proto")
CHART_IMAGE = TMP_DIR / "chart_sample.png"


def redact(s):
    s = str(s)
    return s.replace(TOKEN, "<token>") if TOKEN else s


def call(method, params=None, files=None):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    try:
        if files:
            data = {}
            for k, v in (params or {}).items():
                if isinstance(v, (dict, list, bool)):
                    data[k] = json.dumps(v, ensure_ascii=False)
                else:
                    data[k] = str(v)
            handles = {k: open(p, "rb") for k, p in files.items()}
            try:
                r = requests.post(url, data=data, files=handles, timeout=300)
            finally:
                for h in handles.values():
                    h.close()
        else:
            r = requests.post(url, json=params or {}, timeout=120)
        return r.json()
    except Exception as e:
        return {"ok": False, "description": redact(f"{type(e).__name__}: {e}")}


def cell(text, header=False, align="left"):
    c = {"text": text, "align": align, "valign": "middle"}
    if header:
        c["is_header"] = True
    return c


def p(text):
    return {"type": "paragraph", "text": text}


def bold(t):
    return {"type": "bold", "text": t}


def check_identity():
    me = call("getMe")
    if not me.get("ok"):
        sys.exit(f"getMe failed: {redact(me.get('description'))}")
    r = me["result"]
    if r.get("id") != DEV_ID:
        sys.exit(f"ABORT: Refusing to run on bot ID {r.get('id')}! Expected dev bot {DEV_ID}.")
    print(f"Verified dev bot identity: @{r.get('username')} ({r.get('id')})")


def send_studio_dashboard():
    print("--- 1. Studio Dashboard ---")
    blocks = [
        {"type": "heading", "size": 1, "text": "TEST — Relic & Riddle"},
        p("🧪 Development · rc70"),
        p("✅ **Nothing needs you** right now."),
        {"type": "heading", "size": 3, "text": "Coming up"},
        {
            "type": "table",
            "is_bordered": True,
            "is_striped": True,
            "is_compact": True,
            "cells": [
                [cell(bold("Next run")), cell("in 6 hours", align="right")],
                [cell(bold("Next up")), cell("Derinkuyu Underground City", align="right")],
                [cell(bold("Next post")), cell("Nazca Lines (Friday 6:00 PM)", align="right")],
                [cell(bold("Queue")), cell("14 topics, 9 ready", align="right")],
            ],
        },
        {
            "type": "details",
            "summary": "✅ Health · 6 of 7 runs passed",
            "blocks": [
                {
                    "type": "table",
                    "is_bordered": True,
                    "is_striped": True,
                    "is_compact": True,
                    "cells": [
                        [cell(bold("Typical run")), cell("31 min", align="right")],
                        [cell(bold("Spend")), cell("$18.40 of $60 this month", align="right")],
                        [cell(bold("Voice")), cell("41% of the month used", align="right")],
                    ],
                }
            ],
        },
        {"type": "buttons", "align": "center", "buttons": [
            {"text": "Start next episode", "style": "primary", "callback_data": "mock:start_next"}
        ]},
        {"type": "buttons", "align": "center", "buttons": [
            {"text": "📋 Queue", "callback_data": "mock:queue"},
            {"text": "🗓️ Posting · 3", "callback_data": "mock:posts"},
        ]},
        {"type": "buttons", "align": "center", "buttons": [
            {"text": "🎬 Reviews", "callback_data": "mock:reviews"},
            {"text": "📈 Audience", "callback_data": "mock:audience"},
        ]},
        {"type": "buttons", "align": "center", "buttons": [
            {"text": "🖼️ Pictures", "callback_data": "mock:pictures"},
            {"text": "🎵 Music", "callback_data": "mock:music"},
        ]},
        {"type": "buttons", "align": "center", "buttons": [
            {"text": "🩺 Status", "callback_data": "mock:status"},
            {"text": "❓ Help", "callback_data": "mock:help"},
        ]},
        {"type": "footer", "text": "TEST — Updated just now"},
    ]
    resp = call("sendRichMessage", {
        "chat_id": CHAT,
        "message_thread_id": TOPICS["studio"],
        "rich_message": {"blocks": blocks},
    })
    print("Studio Dashboard result:", resp.get("ok"), resp.get("result", {}).get("message_id") if resp.get("ok") else resp.get("description"))
    return resp


def send_needs_you():
    print("--- 2. Needs You Card & Alert Line ---")
    blocks = [
        {"type": "heading", "size": 2, "text": "TEST — 🔔 Needs you · 4"},
        {"type": "heading", "size": 3, "text": "🎬 Review waiting"},
        p("**Nazca Lines: The Runway Paradox** · made 2 hours ago"),
        p({"type": "italic", "text": "Watch it in 🎬 Reviews and decide there. It times out Saturday 2:00 PM."}),
        {"type": "buttons", "align": "center", "buttons": [
            {"text": "Send it again", "style": "primary", "callback_data": "mock:resend"}
        ]},
        {"type": "heading", "size": 3, "text": "Also waiting"},
        {
            "type": "list",
            "items": [
                {"blocks": [p("2. ❌ Dyatlov Pass failed — 40 minutes ago")]},
                {"blocks": [p("3. 🔑 Instagram token — expires in 3 days")]},
                {"blocks": [p("4. 🧭 Picture search proposal")]},
            ],
        },
        {"type": "buttons", "align": "center", "buttons": [
            {"text": "2", "callback_data": "mock:page_2"},
            {"text": "3", "callback_data": "mock:page_3"},
            {"text": "4", "callback_data": "mock:page_4"},
        ]},
        {"type": "footer", "text": "TEST — Updated just now"},
    ]
    resp_card = call("sendRichMessage", {
        "chat_id": CHAT,
        "message_thread_id": TOPICS["needs"],
        "rich_message": {"blocks": blocks},
    })
    print("Needs You Card result:", resp_card.get("ok"), resp_card.get("result", {}).get("message_id") if resp_card.get("ok") else resp_card.get("description"))

    # Plain new-item notification line (the ONE notification that rings)
    resp_line = call("sendMessage", {
        "chat_id": CHAT,
        "message_thread_id": TOPICS["needs"],
        "text": "TEST — 🎬 Review waiting · Nazca Lines",
        "link_preview_options": {"is_disabled": True},
    })
    print("Needs You Alert Line result:", resp_line.get("ok"), resp_line.get("result", {}).get("message_id") if resp_line.get("ok") else resp_line.get("description"))
    return resp_card, resp_line


def send_review_pair():
    print("--- 3. Review Pair (Video Message + Rich Detail Card) ---")
    if not PROXY_VIDEO.is_file() or not COVER_IMAGE.is_file():
        print(f"Skipping video upload: {PROXY_VIDEO} or {COVER_IMAGE} missing")
        return None, None

    # Message 1: sendVideo with cover and solid inline keyboard
    kb = {
        "inline_keyboard": [
            [
                {"text": "✅ Approve & post", "style": "success", "callback_data": "mock:approve"},
                {"text": "✏️ Edit text", "style": "primary", "callback_data": "mock:edit"},
            ],
            [
                {"text": "🔁 Redo with a note", "style": "primary", "callback_data": "mock:regen"},
                {"text": "🗑 Reject", "style": "danger", "callback_data": "mock:reject"},
            ],
        ]
    }
    video_params = {
        "chat_id": CHAT,
        "message_thread_id": TOPICS["reviews"],
        "supports_streaming": True,
        "reply_markup": kb,
        "caption": "TEST — Nazca Lines: The Runway Paradox",
    }
    video_files = {
        "video": str(PROXY_VIDEO),
        "cover": str(COVER_IMAGE),
    }
    resp_vid = call("sendVideo", video_params, files=video_files)
    print("Review Video result:", resp_vid.get("ok"), resp_vid.get("result", {}).get("message_id") if resp_vid.get("ok") else resp_vid.get("description"))

    # Message 2: Rich Detail Card directly underneath
    rich_blocks = [
        {"type": "heading", "size": 2, "text": "TEST — 🎬 Nazca Lines: The Runway Paradox"},
        p("⚠️ **Ready to approve** · 2 notes to check"),
        p({"type": "italic", "text": "“From the air they look like runways. From the ground, you'd never know they were there.” — Hook, first 3 seconds"}),
        {
            "type": "table",
            "is_bordered": True,
            "is_striped": True,
            "is_compact": True,
            "cells": [
                [cell(bold("Length")), cell("52.3 s", align="right")],
                [cell(bold("Release gate")), cell("✅ All checks passed", align="right")],
                [cell(bold("Pictures")), cell("9 distinct / 10 beats", align="right")],
                [cell(bold("Rights")), cell("✅ All cleared", align="right")],
                [cell(bold("Cost")), cell("~$0.41", align="right")],
            ],
        },
        {
            "type": "details",
            "summary": "⚠️ Worth a look · 2",
            "blocks": [
                {
                    "type": "list",
                    "items": [
                        {"blocks": [p("⚠️ 2 repeated shots — thin photo pool (beats 4 and 9)")]},
                        {"blocks": [p("✅ Beat 8 picture judged against narration — fits")]},
                    ],
                }
            ],
        },
        {
            "type": "details",
            "summary": "📜 Script · 52 s, 142 words",
            "blocks": [
                p("_0:00–0:04_ From the air they look like runways. From the ground, you'd never know they were there."),
                p("_0:04–0:09_ Etched into the arid plateau of southern Peru, more than a thousand geometric figures stretch for miles."),
                p("_0:09–0:14_ The Paracas and Nazca peoples cleared the dark iron-oxide gravel to reveal the pale earth beneath."),
            ],
        },
        {
            "type": "details",
            "summary": "Captions & first comment",
            "blocks": [
                {"type": "heading", "size": 3, "text": "Caption"},
                p("Lines etched across fifty miles of Peruvian desert. Some point straight to water sources beneath the sand."),
                {"type": "heading", "size": 3, "text": "First comment"},
                p("Which Nazca figure fascinates you most? #nazcalines #archaeology #peru #ancientmysteries"),
            ],
        },
        {
            "type": "details",
            "summary": "Pictures · 10 beats",
            "blocks": [
                {
                    "type": "list",
                    "items": [
                        {"blocks": [p("Beat 1: Aerial view of hummingbird · Wikimedia Commons · CC BY-SA 4.0")]},
                        {"blocks": [p("Beat 2: Desert plateau landscape · Wikimedia Commons · CC BY-SA 3.0")]},
                        {"blocks": [p("Beat 3: Spiral geoglyph · Wikimedia Commons · Public Domain")]},
                    ],
                }
            ],
        },
        {
            "type": "details",
            "summary": "Facts & sources · 14",
            "blocks": [
                {
                    "type": "table",
                    "is_bordered": True,
                    "is_striped": True,
                    "is_compact": True,
                    "cells": [
                        [cell(bold("Release gate")), cell("✅ Passed", align="right")],
                        [cell(bold("Facts")), cell("✅ 96% supported", align="right")],
                        [cell(bold("Pictures")), cell("✅ 9 distinct, 10 beats", align="right")],
                    ],
                }
            ],
        },
        {
            "type": "details",
            "summary": "Run details",
            "blocks": [
                {
                    "type": "table",
                    "is_bordered": True,
                    "is_striped": True,
                    "is_compact": True,
                    "cells": [
                        [cell(bold("Written by")), cell("Gemini 2.5 Pro", align="right")],
                        [cell(bold("Voice")), cell("ElevenLabs · Archivist", align="right")],
                        [cell(bold("Music")), cell("Ancient Winds · licensed", align="right")],
                        [cell(bold("Cost")), cell("$0.41", align="right")],
                    ],
                }
            ],
        },
        {"type": "buttons", "align": "center", "buttons": [
            {"text": "🎨 Covers", "style": "primary", "callback_data": "mock:covers"},
            {"text": "📋 Copy run id", "copy_text": {"text": "run_nazca_lines_runway_paradox_1793267092"}},
        ]},
        {"type": "footer", "text": "TEST — 🧪 Dry run · rc70 · made just now"},
    ]
    resp_card = call("sendRichMessage", {
        "chat_id": CHAT,
        "message_thread_id": TOPICS["reviews"],
        "rich_message": {"blocks": rich_blocks},
    })
    print("Review Detail Card result:", resp_card.get("ok"), resp_card.get("result", {}).get("message_id") if resp_card.get("ok") else resp_card.get("description"))
    return resp_vid, resp_card


def send_runs_progress():
    print("--- 4. Runs Progress Card & Outcome Line ---")
    blocks = [
        {"type": "heading", "size": 2, "text": "TEST — ⚡ Run · Nazca Lines"},
        p("⏳ **Step 3 of 5** · review in 12 minutes (2:52 PM)"),
        {
            "type": "list",
            "items": [
                {"blocks": [p("✅ Script, fact check & voice — 6 min")]},
                {"blocks": [p("✅ Music, photos, Stories & carousel — 9 min")]},
                {"blocks": [p("⏳ Graphics, scene plan & video — 3 min so far")]},
                {"blocks": [p("◽ Quality checks")]},
                {"blocks": [p("◽ Review package")]},
            ],
        },
        p({"type": "italic", "text": "_Making the video: beat 6 of 10_"}),
        {"type": "footer", "text": "TEST — 🧪 Dry run: nothing posts, providers still bill · updated just now"},
    ]
    kb = {
        "inline_keyboard": [
            [
                {"text": "⏹ Cancel run", "style": "danger", "callback_data": "mock:cancel"},
                {"text": "⏳ Rendering…", "disabled": {}},
            ]
        ]
    }
    resp_prog = call("sendRichMessage", {
        "chat_id": CHAT,
        "message_thread_id": TOPICS["runs"],
        "rich_message": {"blocks": blocks},
        "reply_markup": kb,
    })
    print("Runs Progress result:", resp_prog.get("ok"), resp_prog.get("result", {}).get("message_id") if resp_prog.get("ok") else resp_prog.get("description"))

    # Outcome line (silent log)
    resp_out = call("sendMessage", {
        "chat_id": CHAT,
        "message_thread_id": TOPICS["runs"],
        "text": "TEST — 📮 Nazca Lines posted · Reel & Short",
        "disable_notification": True,
    })
    print("Runs Outcome Line result:", resp_out.get("ok"), resp_out.get("result", {}).get("message_id") if resp_out.get("ok") else resp_out.get("description"))
    return resp_prog, resp_out


def send_analytics():
    print("--- 5. Analytics Card with Audience Chart ---")
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    # Generate audience chart
    reels = [
        {"run_id": "r1", "title": "Derinkuyu: Cappadocia", "views": {"instagram": 3120, "youtube": 1150, "facebook": 1010}},
        {"run_id": "r2", "title": "Gol Gumbaz Whispering Gallery", "views": {"instagram": 2410, "youtube": 1260, "facebook": 940}},
        {"run_id": "r3", "title": "The Richat Structure", "views": {"instagram": 2630, "youtube": 1080, "facebook": 880}},
        {"run_id": "r4", "title": "Roman Concrete That Heals", "views": {"instagram": 1960, "youtube": 1450, "facebook": 1020}},
        {"run_id": "r5", "title": "Zhang Heng Seismoscope", "views": {"instagram": 2210, "youtube": 1190, "facebook": 760}},
        {"run_id": "r6", "title": "The Piri Reis Map 1513", "views": {"instagram": 1840, "youtube": 1320, "facebook": 910}},
    ]
    audience_chart.render(reels, CHART_IMAGE, sample=True)

    blocks = [
        {"type": "heading", "size": 2, "text": "TEST — 📈 Audience Overview"},
        p("Last 6 Reels measured 7 days post-publication:"),
        {
            "type": "table",
            "is_bordered": True,
            "is_striped": True,
            "is_compact": True,
            "caption": "Platform Performance (7 Days)",
            "cells": [
                [cell(bold("Platform")), cell(bold("Views"), align="right"), cell(bold("Reach"), align="right"), cell(bold("Followers"), align="right")],
                [cell("Instagram"), cell("10.2k", align="right"), cell("7.9k", align="right"), cell("+128", align="right")],
                [cell("YouTube"), cell("6.8k", align="right"), cell("5.1k", align="right"), cell("+90", align="right")],
                [cell("Facebook"), cell("3.5k", align="right"), cell("2.8k", align="right"), cell("+41", align="right")],
            ],
        },
        {
            "type": "photo",
            "photo": {"type": "photo", "media": "attach://chart"},
            "caption": {"text": "7-day view count per platform across recent episodes"},
        },
        {"type": "footer", "text": "TEST — Updated just now"},
    ]
    resp = call("sendRichMessage", {
        "chat_id": CHAT,
        "message_thread_id": TOPICS["studio"],
        "rich_message": {"blocks": blocks},
    }, files={"chart": str(CHART_IMAGE)})
    print("Analytics Card result:", resp.get("ok"), resp.get("result", {}).get("message_id") if resp.get("ok") else resp.get("description"))
    return resp


def send_confirmations():
    print("--- 6. Confirmations & Reason Chips ---")
    blocks = [
        {"type": "heading", "size": 3, "text": "TEST — Reject Nazca Lines?"},
        p("Select a reason to record. The topic returns to **Queue › Restorable**."),
        {"type": "buttons", "align": "center", "buttons": [
            {"text": "Pictures", "callback_data": "mock:rej:pictures"},
            {"text": "Script", "callback_data": "mock:rej:script"},
            {"text": "Voice", "callback_data": "mock:rej:voice"},
        ]},
        {"type": "buttons", "align": "center", "buttons": [
            {"text": "Music", "callback_data": "mock:rej:music"},
            {"text": "Length", "callback_data": "mock:rej:length"},
            {"text": "Facts", "callback_data": "mock:rej:facts"},
        ]},
        {"type": "buttons", "align": "center", "buttons": [
            {"text": "Other", "callback_data": "mock:rej:other"},
            {"text": "← Keep it", "style": "primary", "callback_data": "mock:rej:keep"},
        ]},
        {"type": "footer", "text": "TEST — Expires in 5 minutes"},
    ]
    resp = call("sendRichMessage", {
        "chat_id": CHAT,
        "message_thread_id": TOPICS["studio"],
        "rich_message": {"blocks": blocks},
    })
    print("Confirmation Card result:", resp.get("ok"), resp.get("result", {}).get("message_id") if resp.get("ok") else resp.get("description"))
    return resp


def main():
    check_identity()
    send_studio_dashboard()
    send_needs_you()
    send_review_pair()
    send_runs_progress()
    send_analytics()
    send_confirmations()
    print("\nAll 7 Core Mocks posted successfully to @relicandriddle_dev_bot!")


if __name__ == "__main__":
    main()
