"""
src/archive_film.py
===================
Real archive film for a topic, found where its rights are stated, held for the operator's approval
(operator, 2026-10-28: Archive.org and Europeana as footage sources; real film over AI motion).

  - Internet Archive: films whose own record carries an explicit open licence (public domain mark, CC0, CC BY;
    CC BY-SA only where IMAGES_ALLOW_SHARE_ALIKE allows it). A collection is never enough: Prelinger's rights
    page says "While some Prelinger films are in the public domain, many are not". Broadcast captures
    (tvarchive, tvnews) are never taken.
  - Europeana: video whose holder's rights statement is open and whose record links to a file, not a viewing
    page (most Europeana video is in copyright or educational-only: ERT's Antikythera films are InC-EDU).

Found films are downloaded (HTTPS from the archive's own hosts, size-capped) into assets/<topic>/footage/pending/
with a still and their rights record. Nothing is used until the operator approves it (/footage N use 1); approved
clips join assets/<topic>/footage/, the curated footage every run already uses within FOOTAGE_MAX_SHARE, muted,
one segment per source, credited. Relevant films without an open licence are listed to ask their holder.
"""

import json
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from . import config
from .credits import license_allowed, normalize
from .museum_sources import europeana_search, open_licence

log = logging.getLogger(__name__)

SEARCH = "https://archive.org/advancedsearch.php"
METADATA = "https://archive.org/metadata/"
UA = {"User-Agent": "RelicAndRiddle/1.0 (archive film for history videos; https://github.com/kolade-ogunleye/relic-and-riddle)"}
PAUSE = 1.0                     # archive.org asks automated clients to space their requests
BROADCAST = {"tvarchive", "tvnews"}
COMMUNITY = {"opensource_movies", "opensource", "community_video", "communityvideos", "folksoundomy"}
OPEN_URLS = ["http://creativecommons.org/publicdomain/mark/1.0/", "https://creativecommons.org/publicdomain/mark/1.0/",
             "http://creativecommons.org/publicdomain/zero/1.0/", "https://creativecommons.org/publicdomain/zero/1.0/",
             "http://creativecommons.org/licenses/by/4.0/", "https://creativecommons.org/licenses/by/4.0/",
             "http://creativecommons.org/licenses/by/3.0/", "http://creativecommons.org/licenses/by-sa/4.0/",
             "http://creativecommons.org/licenses/by-sa/3.0/"]
ASK = {"prelinger": "Prelinger Archives: prelingerclips@gmail.com"}
PENDING, DECISIONS = "pending", "decisions.json"


def _get(url: str, params: Optional[dict] = None) -> Optional[dict]:
    time.sleep(PAUSE)
    try:
        r = requests.get(url, params=params, headers=UA, timeout=40)
        if r.status_code == 429:
            log.info("archive.org asked us to slow down; stopping this search.")
            return None
        return r.json() if r.status_code == 200 else None
    except (requests.RequestException, ValueError) as e:
        log.info("archive.org unavailable (%s).", e)
        return None


def _relevant(text: str, keywords: List[str]) -> bool:
    t = normalize(text)
    return any(normalize(k) in t for k in keywords if k)


def _best_file(files: List[dict]) -> Optional[dict]:
    """The h.264 MP4 under the size cap, largest first (the best quality we may take)."""
    cap = config.ARCHIVE_FILM_MAX_MB * 1_000_000
    mp4 = [f for f in files if str(f.get("name", "")).lower().endswith(".mp4") and 0 < int(f.get("size") or 0) <= cap]
    return max(mp4, key=lambda f: int(f.get("size") or 0)) if mp4 else None


def archive_candidates(query: str, keywords: List[str], limit: int = 6) -> Tuple[List[dict], List[dict]]:
    """(films we may use, relevant films to ask about) for one search."""
    licences = " OR ".join(f'"{u}"' for u in OPEN_URLS)
    base = f"({query}) AND mediatype:movies AND NOT collection:({' OR '.join(sorted(BROADCAST))})"  # one query text every time
    usable, ask = [], []
    found = _get(SEARCH, {"q": f"{base} AND licenseurl:({licences})", "fl[]": ["identifier", "title"], "rows": limit,
                          "output": "json"}) or {}
    for doc in (found.get("response") or {}).get("docs", []):
        record = _get(METADATA + doc["identifier"]) or {}
        meta = record.get("metadata") or {}
        collections = set(meta.get("collection") if isinstance(meta.get("collection"), list) else [meta.get("collection")])
        rights = open_licence(str(meta.get("licenseurl") or ""))
        # Named in its title or subject tags: a description mentioning the topic in passing is not footage of it
        # ("First Spaceship On Venus", 1960, matched Tunguska on its description: 2026-10-28)
        subjects = meta.get("subject") if isinstance(meta.get("subject"), list) else [meta.get("subject") or ""]
        text = " ".join([str(meta.get("title", ""))] + [str(s) for s in subjects])
        if collections & BROADCAST or not rights or not license_allowed(rights[0]) or not _relevant(text, keywords):
            continue  # confirmed from the item's own record, not the search index
        best = _best_file(record.get("files") or [])
        if not best:
            continue
        usable.append({"provider": "archive.org", "id": doc["identifier"], "title": str(meta.get("title") or doc["identifier"]),
                       "year": str(meta.get("year") or meta.get("date") or "")[:4], "artist": str(meta.get("creator") or "Internet Archive"),
                       "license": rights[0], "license_url": rights[1], "attribution_required": not rights[0].startswith(("Public", "CC0")),
                       "source_url": f"https://archive.org/details/{doc['identifier']}",
                       "download_url": f"https://archive.org/download/{doc['identifier']}/{best['name']}",
                       "description": re.sub("<[^>]+>", " ", str(meta.get("description") or ""))[:300],
                       "collections": sorted(c for c in collections if c),
                       # A community upload's licence is its uploader's say-so; an institution's is the holder's
                       "community_upload": bool(collections & COMMUNITY) or not (collections - COMMUNITY)})
    for collection, contact in ASK.items():
        found = _get(SEARCH, {"q": f"{base} AND collection:{collection} AND NOT licenseurl:[* TO *]",
                              "fl[]": ["identifier", "title", "year"], "rows": 3, "output": "json"}) or {}
        for doc in (found.get("response") or {}).get("docs", []):
            if _relevant(str(doc.get("title", "")), keywords):
                ask.append({"title": doc.get("title", ""), "year": str(doc.get("year") or ""), "contact": contact,
                            "source_url": f"https://archive.org/details/{doc['identifier']}"})
    return usable, ask


def europeana_candidates(query: str, keywords: List[str]) -> List[dict]:
    out = []
    for c in europeana_search(query, limit=6, media_type="VIDEO"):
        if _relevant(f"{c['title']} {c['description']}", keywords):
            out.append({**c, "provider": "europeana", "id": "eu-" + re.sub(r"\W+", "-", c["source_url"])[-60:]})
    return out


def _decisions(folder: Path) -> Dict[str, str]:
    try:
        return json.loads((folder / DECISIONS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def find(topic: Dict[str, Any], spec: Dict[str, Any], assets_dir: Path, max_clips: int = 4) -> Dict[str, Any]:
    """Searches with the topic's curated queries and keywords, downloads up to max_clips new films into the pending
    folder (a still and a rights record beside each), and returns {"saved": [...], "ask": [...]}."""
    from .footage import download, probe
    folder = Path(assets_dir) / topic["id"] / "footage"
    pending = folder / PENDING
    pending.mkdir(parents=True, exist_ok=True)
    decided = _decisions(folder)
    have = {json.loads(p.read_text(encoding="utf-8")).get("id") for p in pending.glob("*.json")}
    keywords = spec.get("keywords") or [topic.get("title", "")]
    saved, ask, seen = [], [], set()
    for query in (spec.get("queries") or [topic.get("title", "")])[:4]:
        usable, asks = archive_candidates(query, keywords)
        ask += [a for a in asks if a["source_url"] not in {x["source_url"] for x in ask}]
        for cand in usable + europeana_candidates(query, keywords):
            if len(saved) >= max_clips:
                break
            if cand["id"] in seen or cand["id"] in decided or cand["id"] in have:
                continue  # already offered, used or rejected: never fetched twice
            seen.add(cand["id"])
            n = 1 + max([int(p.stem.split("_")[0]) for p in pending.glob("*.mp4") if p.stem.split("_")[0].isdigit()] or [0])
            dest = pending / f"{n:02d}_{re.sub(r'[^A-Za-z0-9-]+', '-', cand['id'])[:50]}.mp4"
            try:
                download(cand["download_url"], "archive" if cand["provider"] == "archive.org" else "europeana", dest,
                         max_bytes=config.ARCHIVE_FILM_MAX_MB * 1_000_000)
                info = probe(dest)
                at = max(1.0, info["duration"] * 0.35)
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-protocol_whitelist", "file", "-ss", f"{at:.1f}", "-i", str(dest),
                                "-frames:v", "1", "-vf", "scale=720:-2", str(dest.with_suffix(".jpg"))], check=True, timeout=120,
                               capture_output=True)
            except Exception as e:
                dest.unlink(missing_ok=True)
                log.info("Film %s not taken: %s", cand["id"], e)
                continue
            record = {k: v for k, v in cand.items() if k != "download_url"}
            record.update(duration=round(info["duration"], 1), width=info["width"], height=info["height"],
                          fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            dest.with_suffix(".json").write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
            saved.append(record)
    return {"saved": saved, "ask": ask[:6]}


def pending_list(assets_dir: Path, topic_id: str) -> List[Tuple[Path, dict]]:
    folder = Path(assets_dir) / topic_id / "footage" / PENDING
    out = []
    for p in sorted(folder.glob("*.mp4")) if folder.is_dir() else []:
        try:
            out.append((p, json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return out


def sheet(items: List[Tuple[Path, dict]], out: Path) -> Optional[Path]:
    """The pending films' stills on one numbered image, as the /photos sheet is, for one look in the chat."""
    from PIL import Image, ImageDraw
    stills = [(n, p.with_suffix(".jpg")) for n, (p, _) in enumerate(items, 1) if p.with_suffix(".jpg").exists()]
    if not stills:
        return None
    w, h, cols = 480, 360, 2
    rows = -(-len(stills) // cols)
    canvas = Image.new("RGB", (cols * w, rows * h), (20, 20, 20))
    draw = ImageDraw.Draw(canvas)
    for i, (n, still) in enumerate(stills):
        with Image.open(still) as im:
            im = im.convert("RGB")
            im.thumbnail((w - 8, h - 8))
            x, y = (i % cols) * w + (w - im.width) // 2, (i // cols) * h + (h - im.height) // 2
            canvas.paste(im, (x, y))
        draw.rectangle([(i % cols) * w + 8, (i // cols) * h + 8, (i % cols) * w + 64, (i // cols) * h + 64], fill=(0, 0, 0))
        draw.text(((i % cols) * w + 28, (i // cols) * h + 22), str(n), fill=(255, 215, 0))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, quality=88)
    return out


def card(assets_dir: Path, topic: Dict[str, Any], ask: Optional[List[dict]] = None, key: str = "N") -> str:
    """What the topic has: films waiting for a decision (numbered as on the sheet), films in use, holders to ask."""
    import html
    e = html.escape
    items = pending_list(assets_dir, topic["id"])
    folder = Path(assets_dir) / topic["id"] / "footage"
    in_use = [p for p in folder.glob("*.mp4")] if folder.is_dir() else []
    lines = [f"🎞 <b>Archive film</b> · {e(topic.get('title') or topic['id'])}"]
    if items:
        lines += ["", f"<b>Waiting for you</b> ({len(items)}): nothing is used until you say so"]
        for n, (_, r) in enumerate(items, 1):
            who = "community upload: its licence is the uploader's say-so" if r.get("community_upload") else "held by " + ", ".join(r.get("collections") or [r.get("provider", "")])[:60]
            lines.append(f"{n}. <a href=\"{e(r['source_url'])}\">{e(r['title'][:70])}</a> · {e(r.get('year') or '')} · {e(r['license'])} · "
                         f"{r.get('duration', 0):.0f} s · {e(who)}")
        lines.append(f"Use: <code>/footage {key} use 1 2</code> · drop: <code>/footage {key} drop 3</code>")
    else:
        lines += ["", f"No film is waiting. Find some: <code>/footage {key} find</code>"]
    lines += ["", f"<b>In use</b>: {len(in_use)} clip(s) (muted, at most {int(config.FOOTAGE_MAX_SHARE * 100)}% of a video, credited)"]
    if ask:
        lines += ["", "<b>Worth asking for</b> (no open licence; ask the holder before using):"]
        lines += [f"• <a href=\"{e(a['source_url'])}\">{e(a['title'][:70])}</a> {e(a.get('year') or '')} · {e(a['contact'])}" for a in ask]
    return "\n".join(lines)


def decide(assets_dir: Path, topic_id: str, numbers: List[int], use: bool) -> List[str]:
    """Approves (use) or rejects pending films by their number in the list; returns their titles. An approved film
    joins the topic's footage with its rights record; a rejected one is deleted. Both are remembered."""
    folder = Path(assets_dir) / topic_id / "footage"
    items = pending_list(assets_dir, topic_id)
    decided = _decisions(folder)
    done = []
    for n in numbers:
        if not 1 <= n <= len(items):
            continue
        path, record = items[n - 1]
        decided[record["id"]] = "used" if use else "dropped"
        if use:
            record["check_note"] = "Found in an open archive; its licence checked; approved by the operator"
            target = folder / path.name.split("_", 1)[-1]
            shutil.move(str(path), target)
            target.with_suffix(".json").write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
            path.with_suffix(".json").unlink(missing_ok=True)
            path.with_suffix(".jpg").replace(target.with_suffix(".jpg")) if path.with_suffix(".jpg").exists() else None
        else:
            for suffix in (".mp4", ".json", ".jpg"):
                path.with_suffix(suffix).unlink(missing_ok=True)
        done.append(record.get("title", path.stem))
    (folder / DECISIONS).write_text(json.dumps(decided, indent=1), encoding="utf-8")
    return done
