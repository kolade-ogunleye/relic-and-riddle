"""Public operator commands. Callback implementation names are not public commands."""
GROUPS = {
    "Everyday": [("dashboard", "Open the dashboard"), ("status", "Full status"), ("progress", "Current or last recorded run"),
                 ("jobs", "Background jobs and cancellation"), ("clear", "Tidy chat; preserve pending reviews"), ("help", "Commands by category"),
                 ("run", "Start a run; publication still needs approval"), ("produce", "Render the next N queued topics back to back, each to review"),
                 ("reviews", "Reviews waiting, post candidates and history"), ("history", "Blocked and approved reviews"), ("digest", "This week's digest"),
                 ("posts", "Posting: bookings, status, links and retries"),
                 ("pause", "Pause scheduled work"), ("resume", "Resume scheduled work"), ("accounts", "Refresh publishing connections"),
                 ("deploy", "Install the newest signed release now (production)")],
    "Topics": [("queue", "Browse the topic queue"), ("next", "Next selected topic"), ("bump", "Prioritize a topic"), ("drop_topic", "Archive a topic"), ("requeue", "Restore a topic"), ("undo", "Undo the last supported queue action"), ("ideas", "Research and review ideas"), ("propose", "Research new ideas"), ("proposals", "Review waiting ideas"), ("discover", "Browse UNESCO themes"), ("add", "Add a topic title")],
    "Media": [("images", "Images: browse, fetch, upload or review"), ("music", "Music: fetch, listen and approve"), ("covers", "Preview and choose a cover"), ("footage", "Upload licensed video footage"), ("review", "Review a topic image set"), ("drop", "Remove selected images"), ("keep", "Use a Context or Unchecked photo anyway"), ("swap", "Swap storyboard beats")],
    "Diagnostics": [("ask", "Ask from recorded evidence"), ("issue", "Report a problem"), ("alerts", "Recent alerts"), ("audit", "Operator activity"), ("improve", "Review improvement proposals"),
                    ("analytics", "How published posts did, every platform")],
}


def menu():
    return [{"command": command, "description": description} for group in GROUPS.values() for command, description in group]


def help_text(group=None):
    groups = {group: GROUPS[group]} if group in GROUPS else GROUPS
    return "\n\n".join("<b>" + title + "</b>\n" + "\n".join(f"/{name} — {description}" for name, description in commands) for title, commands in groups.items())
