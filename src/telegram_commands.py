"""Public operator commands (the "/" menu and Help), grouped by what the operator is doing. Each command has
few arguments and, where a family of actions belongs together, subcommands (/queue bump 4) instead of more
commands. Older names stay as hidden aliases (ALIASES), so nothing the operator typed before is refused."""
GROUPS = {
    "Everyday": [
        ("dashboard", "The one panel: what needs you, then production"),
        ("render", "Make the next topic now · /render 4 for topic 4"),
        ("produce", "Make the next N topics back to back, each to review · /produce 3"),
        ("progress", "The run being made"),
        ("reviews", "Reviews waiting · /reviews history"),
        ("posts", "Bookings and posts · /posts now 5 · move ID · cancel ID · retry ID"),
        ("status", "Full status: production, spend, accounts, issues"),
        ("pause", "Pause scheduled runs"), ("resume", "Resume scheduled runs"),
        ("help", "Commands by group · /help topics"),
    ],
    "Topics": [
        ("queue", "The topic queue · /queue all · bump 4 · drop 4 · requeue 4 · undo · next"),
        ("add", "Add a topic by title · /add The Lost Labyrinth"),
        ("ideas", "Topic ideas · /ideas research · waiting · unesco"),
    ],
    "Pictures and media": [
        ("photos", "Photo sets · /photos 4 · 4 fetch · 4 review · 4 keep 2 7 · 4 drop 1 5 · 4 upload"),
        ("swap", "Retired: the sheet orders photos by what they show"),
        ("covers", "Preview and choose a cover · /covers 4"),
        ("music", "Documentary music · /music find · review · library"),
        ("footage", "Upload licensed video footage"),
    ],
    "Diagnostics": [
        ("jobs", "Background jobs, with Cancel"),
        ("alerts", "Recent alerts"), ("activity", "What the operator did, with times"),
        ("analytics", "How published posts did, every platform"),
        ("improve", "Improvement proposals"), ("digest", "This week's digest"),
        ("accounts", "Check the publishing connections"),
        ("ask", "Ask a question of a run's records · reply to its card"),
        ("issue", "Report a problem · reply to a card to attach it"),
        ("clear", "Tidy the chat; reviews stay"),
        ("deploy", "Install the newest signed release now (production)"),
    ],
}

# Older names and spellings, accepted but not listed: typed, they behave as the new command.
ALIASES = {
    "start": "dashboard", "run": "render", "next": "queue next", "bump": "queue bump", "drop_topic": "queue drop",
    "requeue": "queue requeue", "undo": "queue undo", "history": "reviews history", "propose": "ideas research",
    "proposals": "ideas waiting", "discover": "ideas unesco", "images": "photos", "image": "photos",
    "fetchimages": "photos fetch", "review": "photos review", "drop": "photos drop", "keep": "photos keep",
    "audit": "activity", "critique": "issue", "bug": "issue", "commands": "help",
}


def menu():
    return [{"command": command, "description": description.split(" · ")[0]}
            for group in GROUPS.values() for command, description in group]


def help_text(group=None):
    """One group's commands, each on its own line, its subcommands under it; every group when none is named."""
    groups = {group: GROUPS[group]} if group in GROUPS else GROUPS
    blocks = []
    for name, commands in groups.items():
        lines = [f"<b>{name}</b>"]
        for command, description in commands:
            what, *forms = description.split(" · ")
            lines.append(f"/{command} — {what}")
            if forms:
                lines.append("    " + " · ".join(f"<code>{f if f.startswith('/') else '/' + command + ' ' + f}</code>" for f in forms))
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
    elif command == "render":
        return ("qrender", args) if args else ("run", "")
    elif command == "reviews" and first == "history":
        return "history", rest
    elif command == "posts" and first in ("now", "move", "cancel", "retry") and rest:
        return {"now": "postnow", "move": "movepost", "cancel": "cancelpost", "retry": "retrypost"}[first], rest
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
