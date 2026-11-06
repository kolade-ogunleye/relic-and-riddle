"""QF-44 measurement: SSRF attempts against the real fetch code (src/free_evidence.page_text,
src/commons.download_jpeg, src/image_gen._download_reference, src/footage.download).

Usage: python measure.py <repo root>
The network and DNS are fixtures (requests.get/head and socket.getaddrinfo are replaced; requests' default redirect
following is emulated when the caller does not turn it off), so nothing leaves the machine and the result is the same
on every run. Prints the metric as JSON and writes measurements.json next to this script.
"""

import inspect
import io
import json
import os
import re
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import requests
from PIL import Image

from src import commons, footage, free_evidence, image_gen

SECRET = b"<html><body><p>instance-id i-0123 internal admin console</p></body></html>"
PAGE = b"<html><body><p>The vault under the old mint holds 4,700 tonnes of gold today.</p></body></html>"
DNS = {"cited.example.org": "93.184.216.34", "images.metmuseum.org": "93.184.216.35",
       "holder.example.net": "93.184.216.36", "upload.wikimedia.org": "198.35.26.112",
       "rebound.example.com": "127.0.0.1", "vm.corp-network.example": "10.20.30.40"}
INTERNAL = ["http://127.0.0.1/", "https://127.0.0.1/admin", "https://169.254.169.254/latest/meta-data/",
            "https://10.0.0.5/", "https://192.168.1.1/", "https://172.16.4.4/", "https://[::1]/",
            "https://[::ffff:127.0.0.1]/", "https://2130706433/", "https://0x7f.0.0.1/", "https://localhost/",
            "https://intranet/", "https://printer.local/", "https://rebound.example.com/",
            "https://vm.corp-network.example/"]


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (900, 700), (120, 90, 60)).save(buf, "JPEG")
    return buf.getvalue()


JPEG = _jpeg()


class Resp:
    def __init__(self, url, status, headers, body):
        self.url, self.status_code, self.headers, self.content = url, status, dict(headers), body

    @property
    def is_redirect(self):
        return "Location" in self.headers and self.status_code in (301, 302, 303, 307, 308)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, n):
        yield self.content

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Net:
    def __init__(self, routes):
        self.routes, self.seen = routes, []

    def one(self, method, url):
        self.seen.append(url)
        status, headers, body = self.routes.get(url, (404, {}, b""))
        return Resp(url, status, headers, b"" if method == "HEAD" else body)

    def request(self, method, url, allow_redirects):
        resp, hops = self.one(method, url), 0
        while allow_redirects and resp.is_redirect and hops < 10:
            url, hops = requests.compat.urljoin(url, resp.headers["Location"]), hops + 1
            resp = self.one(method, url)
        return resp


NET = Net({})
requests.get = lambda url, params=None, headers=None, timeout=None, stream=False, allow_redirects=True: \
    NET.request("GET", url, allow_redirects)
requests.head = lambda url, params=None, headers=None, timeout=None, allow_redirects=False: \
    NET.request("HEAD", url, allow_redirects)
commons.time.sleep = lambda s: None


def _getaddrinfo(host, port, *a, **k):
    if host not in DNS:
        raise socket.gaierror("not in the fixture")
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (DNS[host], port))]


socket.getaddrinfo = _getaddrinfo
HAS_PROVIDER = "provider" in inspect.signature(commons.download_jpeg).parameters


def run(routes, call):
    """(outcome, urls requested): outcome 'refused' (None or ValueError) or 'fetched'."""
    NET.routes, NET.seen = routes, []
    try:
        out = call()
        outcome = "refused" if out is None else "fetched"
    except ValueError:
        outcome = "refused"
    return outcome, list(NET.seen)


def download(url, provider=None):
    def call():
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "x.jpg"
            kw = {"provider": provider} if (HAS_PROVIDER and provider) else {}
            commons.download_jpeg(url, dest, **kw)
            return dest.stat().st_size if dest.exists() else None
    return call


def reference(cand):
    def call():
        with tempfile.TemporaryDirectory() as d:
            out = image_gen._download_reference(cand, Path(d))
            return out and out.stat().st_size
    return call


def footage_download(url, provider):
    def call():
        with tempfile.TemporaryDirectory() as d:
            return footage.download(url, provider, Path(d) / "f")
    return call


attacks = []   # (name, routes, call, the URL that must not be requested)
for target in INTERNAL:
    attacks.append((f"page_text 302 -> {target}", {"https://cited.example.org/a": (302, {"Location": target}, b""),
                                                    target: (200, {"Content-Type": "text/html"}, SECRET)},
                    lambda: free_evidence.page_text("https://cited.example.org/a"), target))
    if target.startswith("https://"):
        attacks.append((f"page_text {target}", {target: (200, {"Content-Type": "text/html"}, SECRET)},
                        lambda t=target: free_evidence.page_text(t), target))
off = "https://holder.example.net/x.jpg"
img = {off: (200, {"Content-Type": "image/jpeg"}, JPEG)}
attacks += [
    ("download_jpeg off-host museum url (met)", img, download(off, "met"), off),
    ("download_jpeg off-host url (no provider)", img, download(off), off),
    ("download_jpeg met 302 -> off host", {"https://images.metmuseum.org/a.jpg": (302, {"Location": off}, b""), **img},
     download("https://images.metmuseum.org/a.jpg", "met"), off),
    ("download_jpeg met 302 -> 169.254.169.254",
     {"https://images.metmuseum.org/a.jpg": (302, {"Location": "https://169.254.169.254/x.jpg"}, b""),
      "https://169.254.169.254/x.jpg": (200, {"Content-Type": "image/jpeg"}, JPEG)},
     download("https://images.metmuseum.org/a.jpg", "met"), "https://169.254.169.254/x.jpg"),
    ("download_jpeg europeana host resolving to 127.0.0.1",
     {"https://rebound.example.com/x.jpg": (200, {"Content-Type": "image/jpeg"}, JPEG)},
     download("https://rebound.example.com/x.jpg", "europeana"), "https://rebound.example.com/x.jpg"),
    ("reference off-host museum url", img, reference({"download_url": off, "provider": "met"}), off),
    ("reference met 302 -> 10.0.0.5",
     {"https://images.metmuseum.org/a.jpg": (302, {"Location": "https://10.0.0.5/x.jpg"}, b""),
      "https://10.0.0.5/x.jpg": (200, {"Content-Type": "image/jpeg"}, JPEG)},
     reference({"download_url": "https://images.metmuseum.org/a.jpg", "provider": "met"}), "https://10.0.0.5/x.jpg"),
    ("footage europeana host resolving to 127.0.0.1",
     {"https://rebound.example.com/x.webm": (200, {"Content-Type": "video/webm"}, b"x")},
     footage_download("https://rebound.example.com/x.webm", "europeana"), "https://rebound.example.com/x.webm"),
]
controls = [
    ("page_text public page via public redirect",
     {"https://cited.example.org/a": (301, {"Location": "/b"}, b""),
      "https://cited.example.org/b": (200, {"Content-Type": "text/html"}, PAGE)},
     lambda: free_evidence.page_text("https://cited.example.org/a")),
    ("download_jpeg met file on its host", {"https://images.metmuseum.org/b.jpg": (200, {"Content-Type": "image/jpeg"}, JPEG)},
     download("https://images.metmuseum.org/b.jpg", "met")),
    ("download_jpeg commons file", {"https://upload.wikimedia.org/c.jpg": (200, {"Content-Type": "image/jpeg"}, JPEG)},
     download("https://upload.wikimedia.org/c.jpg")),
    ("reference commons file", {"https://upload.wikimedia.org/c.jpg": (200, {"Content-Type": "image/jpeg"}, JPEG)},
     reference({"download_url": "https://upload.wikimedia.org/c.jpg"})),
]

rows, blocked, leaked = [], 0, 0
for name, routes, call, forbidden in attacks:
    outcome, seen = run(routes, call)
    reached = forbidden in seen
    ok = outcome == "refused" and not reached
    blocked += ok
    leaked += reached
    rows.append({"case": name, "outcome": outcome, "forbidden_url_requested": reached, "blocked": ok})
ctrl_ok = 0
for name, routes, call in controls:
    outcome, seen = run(routes, call)
    ctrl_ok += outcome == "fetched"
    rows.append({"case": f"control: {name}", "outcome": outcome, "expected": "fetched"})

host_check = sorted(set(re.findall(r'provider="([a-z]+)"', (ROOT / "src" / "museum_sources.py").read_text(encoding="utf-8")))
                    - set(footage.ALLOWED_HOSTS))
metric = {
    "ssrf_attempts": len(attacks),
    "ssrf_blocked": blocked,
    "internal_or_off_host_requests_made": leaked,
    "acceptance_redirect_to_127_0_0_1_page_text_none": rows[0]["blocked"],
    "acceptance_off_host_museum_download_raises": next(r["blocked"] for r in rows if r["case"].startswith("download_jpeg off-host museum")),
    "museum_providers_without_host_allowlist": host_check,
    "controls_fetched": f"{ctrl_ok}/{len(controls)}",
}
result = {"metric": metric, "cases": rows}
(Path(__file__).resolve().parent / "measurements.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
print(json.dumps(metric, indent=2))
