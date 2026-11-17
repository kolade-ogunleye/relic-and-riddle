"""No operator or infrastructure identifier is tracked (QF-49, docs/reviews/project-quality-review-2026-11-05.md 9.2).

The row's acceptance grep, over every tracked text file: the operator's user name, the lab's domain suffix, the
GitHub handle and webmail addresses, plus the production host's short name and the lab jump host's address. Paths
are written repo-relative, the production host as <production-host>, contacts through settings (BRAND_CONTACT,
COMMONS_CONTACT). Each pattern carries one bracketed letter so this file does not match itself.

Git history is out of scope (a rewrite of published history is the operator's decision, not a code change).
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DENY = (
    "omokolad[e]",            # the operator's Windows user name (in paths: C:\Users\<user>\...)
    r"lab\.internal",         # the lab's DNS suffix (production host FQDN)
    "kolade-ogunley[e]",      # the operator's GitHub handle
    r"@gmail\.com",           # webmail addresses
    "content-client-win-0[1]",  # the production host's short name
    r"10\.10\.20\.2[0]",      # the lab jump host
)

GMAIL = "@" + "gmail.com"   # split so this file does not match itself

# Genuine third-party attribution only, each by file and the exact text of its line (line numbers move).
ALLOW = {
    ("src/archive_film.py", 'ASK = {"prelinger": "Prelinger Archives: prelingerclips' + GMAIL + '"}'):
        "Prelinger Archives' published licensing contact, the rights holder the ask card names; a third party's "
        "public address, not the operator's.",
    ("tests/test_archive_film.py", ('assert ask == [{"title": "Father Nile (Pompeii edition)", "year": "1931", '
                                    f'"contact": "Prelinger Archives: prelingerclips{GMAIL}",')):
        "The test of that ask card's rights-holder contact (above).",
    ("tests/test_archive_film.py", ('assert "Waiting for you" in card and "Pompeii And Vesuvius" in card and '
                                    f'"prelingerclips{GMAIL}" in card')):
        "The test of that ask card's rights-holder contact (above).",
}


def tracked_hits(root: Path = ROOT) -> list[tuple[str, int, str]]:
    """(path, line, text) of every tracked text line matching DENY (case-insensitive), via git grep."""
    cmd = ["git", "-C", str(root), "grep", "-nIiE", "--full-name"]
    for pattern in DENY:
        cmd += ["-e", pattern]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    assert out.returncode in (0, 1), out.stderr          # 1: no match
    hits = []
    for line in out.stdout.splitlines():
        path, number, text = line.split(":", 2)
        hits.append((path, int(number), text.strip()))
    return hits


def offending(root: Path = ROOT) -> list[str]:
    return [f"{p}:{n}: {t[:120]}" for p, n, t in tracked_hits(root) if (p, t) not in ALLOW]


def test_no_operator_or_infrastructure_identifier_is_tracked():
    bad = offending()
    assert not bad, "Operator or infrastructure identifiers tracked:\n" + "\n".join(bad)


def test_every_allowlisted_line_is_still_there_and_justified():
    """The allowlist stays small and current: each entry names a line that exists and says why it is attribution."""
    hits = {(p, t) for p, _, t in tracked_hits()}
    stale = [f"{p}: {t}" for p, t in ALLOW if (p, t) not in hits]
    assert not stale, "Allowlisted lines no longer tracked (remove them):\n" + "\n".join(stale)
    assert all(len(why) > 40 for why in ALLOW.values())
