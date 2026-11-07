"""One-time consent for YouTube's audience-retention reports (src/youtube_analytics.py).

Run on your own machine (it opens your browser), signed in as the YouTube channel's owner:

    python scripts/youtube_analytics_auth.py --client-id <id> --client-secret <secret>

The client is an OAuth client of type "Desktop app" in a Google Cloud project with the YouTube Analytics API
enabled (console.cloud.google.com: APIs & Services). It asks for one scope, yt-analytics.readonly, which reads
reports and cannot change anything. It prints the three lines to add to production's .env.
"""
from __future__ import annotations

import argparse
import http.server
import secrets
import sys
import threading
import urllib.parse
import webbrowser

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not (1024 <= args.port <= 65535):
        parser.error("--port must be between 1024 and 65535")

    redirect = f"http://127.0.0.1:{args.port}/"
    state = secrets.token_urlsafe(24)
    got: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # the stdlib's handler name
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state", [""])[0] == state and query.get("code"):
                got["code"] = query["code"][0]
                message = "Done. You can close this tab and return to the terminal."
            else:
                got["error"] = query.get("error", ["no code or a mismatched state"])[0]
                message = f"Consent not completed: {got['error']}"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(message.encode())

        def log_message(self, *a):  # quiet
            pass

    server = http.server.HTTPServer(("127.0.0.1", args.port), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": args.client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state})
    print("Opening your browser for Google's consent page. If it does not open, visit:\n" + url + "\n")
    webbrowser.open(url)
    thread.join(timeout=300)
    server.server_close()
    if "code" not in got:
        print(f"No consent received: {got.get('error', 'timed out after 5 minutes')}", file=sys.stderr)
        return 1
    r = requests.post(TOKEN_URL, data={"code": got["code"], "client_id": args.client_id,
                                       "client_secret": args.client_secret, "redirect_uri": redirect,
                                       "grant_type": "authorization_code"}, timeout=30)
    if r.status_code >= 400 or "refresh_token" not in r.json():
        print(f"Google did not issue a refresh token (HTTP {r.status_code}): {r.text[:300]}", file=sys.stderr)
        return 1
    print("Add these to production's .env (keep them secret):\n")
    print(f"YOUTUBE_ANALYTICS_CLIENT_ID={args.client_id}")
    print(f"YOUTUBE_ANALYTICS_CLIENT_SECRET={args.client_secret}")
    print(f"YOUTUBE_ANALYTICS_REFRESH_TOKEN={r.json()['refresh_token']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
