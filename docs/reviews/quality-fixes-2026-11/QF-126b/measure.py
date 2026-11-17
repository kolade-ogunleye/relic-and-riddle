"""QF-126b acceptance metric: complete credits that fit the caption.

Usage: python measure.py <repo root>. Prints the metric as JSON. Runs the real code of the repo given (the pipeline's
own add_provenance_attributions, the release gate's ledger_credit_problems and the publishers' field builders through
publishable.credit_texts) on the development runs QF-126 measured, as committed in
tests/fixtures/qf126b/dev_runs.json (their stored copy, their ledgers' credit fields, their music), each staged in a
temporary folder. No network, no output/ folder, nothing written in the repo. It runs on the commit before the change
too (it uses only names that commit has; copy the two fixture files into that tree).

What a complete credit is, is decided here and not by the code under test: the lines of each text a platform
receives are read by this file's own reader. A photo is credited when one line names its author, its title, its
licence, a link to its own page and a link to its licence: either the one-photo line
("📷 author | title | licence | page link | licence link") or the line of its licence
("📷 licence licence-link | author: “title” page-link | “title” page-link | author: ..."). A link by page id
(https://commons.wikimedia.org/?curid=<id>) counts only when it is the id the Commons API gave for that photo's
page (tests/fixtures/qf126b/dev_page_ids.json). A 📷 or 🎵 line that does not read whole is counted as cut.

`credited_full_links`: the pipeline's result without a lookup (the form alone). `credited`: its result with the
lookup of page ids available (served here from the fixture: no network); on the commit before the change, as built.

With RR_REAL_OUTPUT set to a folder of real run_* folders (read only: each run's ledger, copy and rights record are
copied to a temporary folder), the same counts for those runs, without the per-run rows. RR_PAGE_IDS names a JSON file
{"page_ids": {source link: page id}} for them (without it, no page id is known and `credited` equals
`credited_full_links`).
"""

import copy
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

NO_CREDIT = ("cc0", "pdm", "pd", "public domain", "publicdomain")
URL = r"https?://[^\s|<>\"]+"
ENTRY = re.compile(r"^(?:(?P<author>.+?): )?“(?P<title>.*)” (?P<link>" + URL + r")$")
PARTS = ("author", "title", "page_link", "licence_name", "licence_link")


# ---------------------------------------------------------------- the runs

def fixture_runs(root):
    data = json.loads((root / "tests" / "fixtures" / "qf126b" / "dev_runs.json").read_text(encoding="utf-8"))
    ids = json.loads((root / "tests" / "fixtures" / "qf126b" / "dev_page_ids.json").read_text(encoding="utf-8"))
    return [{"name": r["run"], "payload": r["payload"], "ledger": {"assets": r["assets"]}, "music": r["music"]}
            for r in data["runs"]], ids["page_ids"]


def real_runs(folder):
    out = []
    for run in sorted(Path(folder).glob("run_*")):
        try:
            ledger = json.loads((run / "provenance.json").read_text(encoding="utf-8"))
            payload = json.loads((run / "script_metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        music = []
        bed = (ledger.get("ambient_bed") or {}).get("path")
        if bed:
            try:
                name = str(bed).replace("\\", "/").rsplit("/", 1)[-1] + ".rights.json"
                music = [s for s in json.loads((run / name).read_text(encoding="utf-8")).get("sources") or []
                         if isinstance(s, dict) and s.get("title")]
            except (OSError, ValueError):
                music = []
        out.append({"name": run.name, "payload": payload, "ledger": {"assets": ledger.get("assets") or []}, "music": music})
    return out


def stage(folder, run):
    """The run's ledger and its bed's rights record in a folder of their own: what the credits are written from."""
    folder.mkdir(parents=True)
    ledger = {"assets": run["ledger"]["assets"]}
    if run["music"]:
        ledger["ambient_bed"] = {"path": "ambient_bed.wav"}
        (folder / "ambient_bed.wav.rights.json").write_text(json.dumps({"sources": run["music"]}), encoding="utf-8")
    (folder / "provenance.json").write_text(json.dumps(ledger), encoding="utf-8")
    return folder / "provenance.json"


# ---------------------------------------------------------------- what must be credited (independent)

def photos_asked(run):
    """Each photo whose licence asks for a credit, with the parts its credit must show."""
    from src.credits import author_name, credit_title
    records = list(run["ledger"]["assets"])
    for asset in run["ledger"]["assets"]:
        for ref in (asset.get("illustration") or {}).get("references") or []:
            if isinstance(ref, dict):
                records.append({"artist": ref.get("artist") or "Unknown", "title": ref.get("title"),
                                "license": ref.get("licence"), "source_url": ref.get("url"),
                                "license_url": ref.get("license_url")})
    out = []
    for r in records:
        licence = " ".join(str(r.get("license") or "").split())
        if not (r.get("attribution_required") or licence.lower().startswith("cc by")):
            continue
        source = str(r.get("source_url") or "")
        work = {"author": author_name(r), "title": credit_title(r), "licence_name": licence, "page_link": source,
                "licence_link": str(r.get("license_url") or ""), "own_work": source.lower().startswith("own work")}
        if work not in out:
            out.append(work)
    return out


def tracks_asked(run):
    out = []
    for t in run["music"]:
        licence = str(t.get("license") or "").lower()
        if not licence or licence.startswith(NO_CREDIT):
            continue
        name = "CC BY" if licence in ("by", "cc by") or licence.startswith(("by ", "cc by ")) else licence.upper()
        parts = (str(t.get("title")), str(t.get("creator")), name, str(t.get("license_version") or ""),
                 str(t.get("source_url") or "\x00"), str(t.get("license_url") or "\x00"), "edited")
        if parts not in out:
            out.append(parts)
    return out


# ---------------------------------------------------------------- what a text credits (independent reader)

def read_photo_lines(text):
    """([each photo a 📷 line credits: {author, title, licence_name, page_link, licence_link, form}], the number of
    📷 lines that are neither a whole credit of either form nor the summary line)."""
    works, not_whole = [], 0
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("📷") or line.startswith("📷 Images via"):
            continue
        fields = [f.strip() for f in line.removeprefix("📷").split(" | ")]
        head = fields[0].rsplit(" ", 1)
        if len(fields) == 5 and all(re.fullmatch(URL, f) or f == "Unknown" for f in fields[3:]):
            works.append({"author": fields[0], "title": fields[1], "licence_name": fields[2], "page_link": fields[3],
                          "licence_link": fields[4], "form": "photo"})
            continue
        entries = [ENTRY.match(f) for f in fields[1:]]
        if len(head) != 2 or not re.fullmatch(URL, head[1]) or not entries or not all(entries) \
                or not entries[0].group("author"):
            not_whole += 1
            continue
        author = ""
        for entry in entries:
            author = entry.group("author") or author
            works.append({"author": author, "title": entry.group("title"), "licence_name": head[0],
                          "page_link": entry.group("link"), "licence_link": head[1], "form": "licence"})
    return works, not_whole


def same_licence_link(a, b):
    """One deed: creativecommons.org serves it over http and https, with or without the final "/"."""
    def key(u):
        m = re.fullmatch(r"https?://(?:www\.)?creativecommons\.org(/.*?)/?", u, re.IGNORECASE)
        return m.group(1).lower() if m else u
    return bool(a) and key(a) == key(b)


def photo_parts(work, credited, page_ids):
    """Which of the five parts some line gives for `work` (the best line: the one that links to its page)."""
    by_id = page_ids.get(work["page_link"])
    best = dict.fromkeys(PARTS, False)
    for c in credited:
        title = work["title"]
        linked = c["page_link"] == work["page_link"] or (
            by_id is not None and c["page_link"] == f"https://commons.wikimedia.org/?curid={by_id}")
        if work["own_work"]:   # the operator's own photograph has no page: its line names it and its licence
            linked = c["title"] == title and c["author"] == work["author"]
        has = {"author": c["author"] == work["author"], "title": c["title"] == title, "page_link": linked,
               "licence_name": c["licence_name"] == work["licence_name"],
               "licence_link": same_licence_link(c["licence_link"], work["licence_link"])}
        if linked and sum(has.values()) > sum(best.values()):
            best = has
    return best


def wrong_page_ids(credited, photos, page_ids):
    """Links by page id that are neither the id recorded for a photo the run must credit nor a photo's own source
    link as its record holds it (some records' source link is itself a link by page id)."""
    allowed = {f"https://commons.wikimedia.org/?curid={page_ids[w['page_link']]}" for w in photos if w["page_link"] in page_ids}
    allowed |= {w["page_link"] for w in photos}
    return sum("curid=" in c["page_link"] and c["page_link"] not in allowed for c in credited)


def other_credits(text, photos, tracks):
    """(carried, asked) of the notices and the music: the adapted notice, the share-alike notice, each track whole."""
    lines = (text or "").splitlines()
    asked = carried = 0
    if photos:
        asked += 1
        carried += any("adapted" in line and line.startswith(("📷", "🎬")) for line in lines)
    if any(re.match(r"cc by-sa [2-4]", w["licence_name"], re.IGNORECASE) for w in photos):
        asked += 1
        carried += any(line.startswith("🎬") and "CC BY-SA 4.0" in line
                       and "https://creativecommons.org/licenses/by-sa/4.0/" in line for line in lines)
    for parts in tracks:
        asked += 1
        carried += any(line.startswith("🎵") and all(p in line for p in parts) for line in lines)
    return carried, asked


# ---------------------------------------------------------------- the pipeline's result for a run

def credit(run, folder, page_ids, lookup):
    """(held, {text name: string}, {name: limit}, payload, lookups made) for the run credited by the pipeline."""
    from src import credits, publishable
    ledger = stage(folder, run)
    payload = copy.deepcopy(run["payload"])
    calls = []
    if lookup:
        def find(urls):
            calls.append(len(urls))
            return {u: page_ids[u] for u in urls if u in page_ids}
        credits.add_provenance_attributions(payload, ledger, lookup=find)
    else:
        credits.add_provenance_attributions(payload, ledger)
    youtube = publishable.is_documentary(payload)
    meta = publishable.envelope_metadata({**payload, "title": payload.get("title") or ""}, None)
    texts, limits = publishable.credit_texts(meta, youtube)
    held = bool(credits.ledger_credit_problems(ledger, texts, limits))
    return held, texts, limits, payload, len(calls)


def section(runs, tmp, page_ids, lookup, label, rows=None):
    out = {"held_for_credits": 0, "sent": 0, "sent_with_every_credit_complete_in_every_text": 0,
           "sent_lacking_a_credit": 0, "texts_sent_over_their_limit": 0, "credit_lines_cut_in_texts_sent": 0,
           "photo_credits_in_texts_sent": {"asked": 0, **dict.fromkeys(PARTS, 0), "complete": 0},
           "notices_and_music_in_texts_sent": {"asked": 0, "carried": 0},
           "runs_using_one_line_a_licence": 0, "links_by_page_id": 0, "links_by_page_id_not_the_photos_page": 0,
           "lookups": 0, "held_runs_with_a_credit_line_cut": 0}
    for n, run in enumerate(runs):
        photos, tracks = photos_asked(run), tracks_asked(run)
        if not photos and not tracks:
            continue
        held, texts, limits, payload, lookups = credit(run, tmp / label / str(n), page_ids, lookup)
        out["lookups"] += lookups
        if rows is not None:
            rows[n][label] = "held" if held else "sent"
        if held:
            out["held_for_credits"] += 1
            # a held caption is still never cut: every credit line it carries is whole
            out["held_runs_with_a_credit_line_cut"] += int(any(
                line.startswith("📷") and " | " in line and not read_photo_lines(line)[0]
                for line in payload.get("caption", "").splitlines()))
            continue
        out["sent"] += 1
        complete = True
        used_licence_line = False
        for name, text in texts.items():
            credited, not_whole = read_photo_lines(text)
            out["texts_sent_over_their_limit"] += int(len(text) > limits[name])
            out["credit_lines_cut_in_texts_sent"] += not_whole
            out["links_by_page_id_not_the_photos_page"] += wrong_page_ids(credited, photos, page_ids)
            used_licence_line = used_licence_line or any(c["form"] == "licence" for c in credited)
            if name == next(iter(texts)):   # links the pipeline wrote by a looked-up id (not a record's own link)
                out["links_by_page_id"] += sum("curid=" in c["page_link"]
                                               and c["page_link"] not in {w["page_link"] for w in photos} for c in credited)
            for work in photos:
                has = photo_parts(work, credited, page_ids)
                whole = all(has.values())
                complete = complete and whole
                if name == next(iter(texts)):   # counted once a run: the Reel's caption (the documentary's description)
                    tally = out["photo_credits_in_texts_sent"]
                    tally["asked"] += 1
                    tally["complete"] += int(whole)
                    for part in PARTS:
                        tally[part] += int(has[part])
            carried, asked = other_credits(text, photos, tracks)
            complete = complete and carried == asked
            if name == next(iter(texts)):
                out["notices_and_music_in_texts_sent"]["asked"] += asked
                out["notices_and_music_in_texts_sent"]["carried"] += carried
        out["runs_using_one_line_a_licence"] += int(used_licence_line)
        out["sent_with_every_credit_complete_in_every_text" if complete else "sent_lacking_a_credit"] += 1
    return out


# ---------------------------------------------------------------- the size of each form

def sizes(run, page_ids):
    """Characters of the run's required credits (each line and the blank line before it) in each form, and where the
    one-photo lines' characters go."""
    from src import credits
    data = {"assets": run["ledger"]["assets"]}
    required = credits.required_credits(data, None)
    photo_lines = [c["line"] for c in required if c["kind"] == "image"]
    rest = sum(len(c["line"]) + 2 for c in required if c["kind"] != "image")
    parts = dict.fromkeys(("author", "title", "licence_name", "page_link", "licence_link", "marks_and_separators"), 0)
    for line in photo_lines:
        fields = [f.strip() for f in line.removeprefix("📷").split(" | ")]
        if len(fields) == 5:
            for key, value in zip(("author", "title", "licence_name", "page_link", "licence_link"), fields):
                parts[key] += len(value)
            parts["marks_and_separators"] += len(line) + 2 - sum(len(f) for f in fields)
    from src.music import credit_line
    music = sum(len(line) + 2 for line in dict.fromkeys(credit_line(t) for t in run["music"]) if line)
    out = {"one_line_a_photo": sum(len(line) + 2 for line in photo_lines) + rest + music,
           "one_line_a_licence_full_links": None, "one_line_a_licence_page_id_links": None}
    if hasattr(credits, "compact_photo_credits"):
        for key, ids in (("one_line_a_licence_full_links", {}), ("one_line_a_licence_page_id_links", page_ids)):
            records = [{**c["record"], **({"pageid": ids[str(c["record"].get("source_url"))]}
                                           if str(c["record"].get("source_url")) in ids else {})}
                       for c in required if c["kind"] == "image"]
            lines, covered = credits.compact_photo_credits(records)
            out[key] = (sum(len(line) + 2 for line in lines)
                        + sum(len(line) + 2 for line in photo_lines if line not in covered) + rest + music)
    return out, parts, len(photo_lines)


def measure(runs, tmp, page_ids, per_run):
    from src import credits
    runs = [r for r in runs if photos_asked(r) or tracks_asked(r)]
    after = hasattr(credits, "compact_photo_credits")
    rows = [{"run": r["name"]} for r in runs] if per_run else None
    totals = {"one_line_a_photo": [], "one_line_a_licence_full_links": [], "one_line_a_licence_page_id_links": []}
    where = {}
    photos = 0
    for n, run in enumerate(runs):
        size, parts, count = sizes(run, page_ids)
        photos += count
        for key, value in size.items():
            if value is not None:
                totals[key].append(value)
        for key, value in parts.items():
            where[key] = where.get(key, 0) + value
        if rows is not None:
            works = photos_asked(run)
            rows[n].update(photos=count, licences=len({(w["licence_name"], w["licence_link"].rstrip("/")) for w in works}),
                           authors=len({w["author"] for w in works}), credit_characters=size)
    out = {"runs_asking_a_credit": len(runs), "photos_to_credit": photos,
           "music_tracks_to_credit": sum(len(tracks_asked(r)) for r in runs),
           "credit_characters": {key: ({"all_runs": sum(v), "longest_run": max(v), "runs_over_1500": sum(x > 1500 for x in v)}
                                       if v else None) for key, v in totals.items()},
           "one_line_a_photo_characters_by_part": where}
    if after:
        out["credited_full_links"] = section(runs, tmp, {}, False, "credited_full_links", rows)
    out["credited"] = section(runs, tmp, page_ids, after, "credited", rows)
    if rows is not None:
        out["per_run"] = rows
    return out


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(root))
    os.chdir(root)
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    tmp = Path(tempfile.mkdtemp(prefix="qf126b-"))
    try:
        if os.environ.get("RR_REAL_OUTPUT"):
            ids = {}
            if os.environ.get("RR_PAGE_IDS"):
                ids = json.loads(Path(os.environ["RR_PAGE_IDS"]).read_text(encoding="utf-8"))["page_ids"]
            result = {"real": measure(real_runs(os.environ["RR_REAL_OUTPUT"]), tmp, ids, per_run=False)}
        else:
            runs, ids = fixture_runs(root)
            result = {"development_runs": measure(runs, tmp, ids, per_run=True)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
