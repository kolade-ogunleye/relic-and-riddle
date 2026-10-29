"""Public operator commands (the "/" menu and Help), grouped by what the operator is doing. Each command has
few arguments and, where a family of actions belongs together, subcommands (/queue bump 4) instead of more
commands. The older names were removed on 2026-10-25: every screen and message
names the current form, and a retired name is answered as unknown, with the way to /help."""
GROUPS = {
    "Everyday": [
        ("dashboard", "The one panel: what needs you, then production"),
        ("render", "Make the next topic now · 4 · fresh script · reuse 12 · [a number: that topic, moved to the front; "
                   "fresh STEP: make that step and the later ones again (facts, script, voice, music, clips); "
                   "reuse N: what run N made and passed, for its topic]"),
        ("produce", "Make the next N topics back to back, each to review · 3"),
        ("progress", "The run being made"),
        ("reviews", "Reviews waiting · history"),
        ("posts", "Bookings and posts · slots · now 5 · move 5 · cancel 5 · retry 5 · [5: the episode's number on Posting]"),
        ("status", "Full status: production, spend, accounts, issues"),
        ("pause", "Pause scheduled runs"), ("resume", "Resume scheduled runs"),
        ("help", "Commands by group · topics · pictures · diagnostics"),
    ],
    "Topics": [
        ("queue", "The topic queue · all · bump 4 · drop 4 · requeue 4 · undo · next"),
        ("add", "Add a topic by title · The Lost Labyrinth"),
        ("ideas", "Topic ideas · research · waiting · unesco"),
    ],
    "Pictures and media": [
        ("photos", "Photo sets · 4 · 4 fetch · 4 review · 4 keep 2 7 · 4 unkeep 2 · 4 drop 1 5 · 4 upload"),
        ("covers", "Preview and choose a cover · 4"),
        ("music", "Documentary music · find · review · library"),
        ("footage", "Archive film and your clips · 4 · 4 find · 4 use 1 2 · 4 drop 3 · [4: the topic's archive film, waiting and in use; "
                    "find: search the Internet Archive and Europeana for openly licensed film; to upload your own, send the clip "
                    "with the caption /footage 4 | source: URL | licence: CC BY 4.0 | licence_url: URL, up to 20 MB]"),
    ],
    "Diagnostics": [
        ("jobs", "Background jobs, with Cancel"),
        ("alerts", "Recent alerts"), ("activity", "What the operator did, with times"),
        ("analytics", "Audience dashboard, every platform · [/analytics ig|fb|yt|ytlong|stories, "
                      "/analytics videos, /analytics N]"),
        ("improve", "Improvement proposals"), ("digest", "This week's digest"),
        ("accounts", "Check the publishing connections"),
        ("ask", "Ask a question of a run's records · [reply to its card with /ask and the question]"),
        ("issue", "Report a problem · [reply to a card to attach it]"),
        ("clear", "Tidy the chat; reviews stay"),
        ("deploy", "Install the newest signed release now (production)"),
    ],
}

# Telegram's own first command (sent when the chat is opened) is the Dashboard. No other aliases: the older
# names were removed on 2026-10-25 (operator: no retired command should still be mentioned or half-work).
ALIASES = {"start": "dashboard"}


def menu():
    return [{"command": command, "description": description.split(" · ")[0]}
            for group in GROUPS.values() for command, description in group]


def find_group(name):
    """The group a typed or tapped name means: any case, its first word or a prefix (/help topics, pictures, diag)."""
    wanted = (name or "").strip().lower()
    if not wanted:
        return None
    return next((g for g in GROUPS if g.lower() == wanted), None) or next(
        (g for g in GROUPS if g.lower().startswith(wanted) or g.lower().split()[0] == wanted), None)


def help_text(group=None):
    """One group's commands, each on its own line, its forms under it (three to a line, each one copyable:
    "/render 4"), and a note in [brackets] in italics, never as a command to copy (review, 2026-10-25: "/ask
    reply to its card" was offered as a command); every group when none is named."""
    import html
    groups = {group: GROUPS[group]} if group in GROUPS else GROUPS
    blocks = []
    for name, commands in groups.items():
        lines = [f"<b>{name}</b>"]
        for command, description in commands:
            what, *rest = description.split(" · ")
            forms = [f"<code>/{command} {f}</code>" for f in rest if not f.startswith("[")]
            notes = [f"<i>{html.escape(f.strip('[]'))}</i>" for f in rest if f.startswith("[")]
            lines.append(f"/{command} — {what}")
            for i in range(0, len(forms), 3):
                lines.append("    " + "   ".join(forms[i:i + 3]))
            lines += ["    " + n for n in notes]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def normalise_command(command, args):
    """(command, args) as the handler knows them: an alias becomes its new form, and a subcommand of /queue,
    /render, /reviews, /posts, /photos, /ideas, /music or /activity becomes the action it stands for.
    Idempotent, so the bot and the handler may both apply it."""
    command, args = (command or "").strip().lower(), (args or "").strip()
    if command in ALIASES:
        head, _, rest = ALIASES[command].partition(" ")
        command, args = head, (rest + " " + args).strip()
    words = args.split()
    first = words[0].lower() if words else ""
    rest = " ".join(words[1:])
    if command == "queue":
        if first in ("bump", "drop", "requeue", "undo", "next", "add"):
            return {"bump": "bump", "drop": "drop_topic", "requeue": "requeue", "undo": "undo", "next": "next", "add": "add"}[first], rest
    elif command == "footage" and words and "|" not in args:  # an upload's caption carries "|" and stays as it is
        action = words[1].lower() if len(words) > 1 else ""
        if action in ("find", "use", "drop"):
            return {"find": "filmfind", "use": "filmuse", "drop": "filmdrop"}[action], " ".join([words[0]] + words[2:])
        if len(words) == 1:
            return "filmlist", words[0]
    elif command == "render":
        if first in ("fresh", "reuse"):
            return "runopt", args
        return ("qrender", args) if args else ("run", "")
    elif command == "reviews" and first == "history":
        return "history", rest
    elif command == "posts" and first == "slots":
        return "cslots", rest
    elif command == "posts" and first in ("now", "move", "cancel", "retry") and rest:
        return "postsdo", f"{first} {rest}"      # rest: the episode's number on Posting
    elif command == "photos":
        verbs = {"fetch": "fetchimages", "review": "review", "keep": "keep", "drop": "drop", "unkeep": "unkeep", "upload": "upload"}
        if not words:
            return "images", ""
        if first in verbs:  # /photos review 4
            return verbs[first], rest
        if len(words) > 1 and words[1].lower() in verbs:  # /photos 4 review · /photos 4 keep 2 7
            return verbs[words[1].lower()], " ".join([words[0]] + words[2:]) if words[1].lower() in ("keep", "drop", "unkeep") else words[0]
        return "image", words[0]
    elif command == "ideas":
        if first in ("research", "waiting", "unesco"):
            return {"research": "propose", "waiting": "proposals", "unesco": "discover"}[first], rest
    elif command == "music" and first in ("find", "library"):
        return "music", ("fetch " + rest).strip() if first == "find" else "approved"
    elif command == "activity":
        return "audit", args
    return command, args
