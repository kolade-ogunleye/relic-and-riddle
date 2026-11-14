"""QF-44: server-side request forgery through the cited-page fetch and the picture downloads.

The network is a fixture (requests.get/head answer from a route table that behaves like requests, following redirects
itself unless allow_redirects=False) and DNS answers from a table; nothing leaves the machine. Each test asserts the
URLs that were actually requested."""

import io
import re
import socket
from pathlib import Path

import pytest
import requests
from PIL import Image

from src import commons, footage, free_evidence, image_gen

PAGE = b"<html><body><p>The vault under the old mint holds 4,700 tonnes of gold today.</p></body></html>"
SECRET = b"<html><body><p>instance-id i-0123 internal admin console</p></body></html>"


def _jpeg(size=(900, 700)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (120, 90, 60)).save(buf, "JPEG")
    return buf.getvalue()


class _Resp:
    def __init__(self, url, status=200, headers=None, body=b""):
        self.url, self.status_code, self.headers, self.content = url, status, dict(headers or {}), body

    @property
    def is_redirect(self):
        return "Location" in self.headers and self.status_code in (301, 302, 303, 307, 308)

    ok = property(lambda self: self.status_code < 400)

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


class _Net:
    """Routes: url -> (status, headers, body). A url not routed is a 404; every request is recorded."""

    def __init__(self, routes):
        self.routes, self.seen = routes, []

    def _one(self, method, url):
        self.seen.append((method, url))
        status, headers, body = self.routes.get(url, (404, {}, b""))
        return _Resp(url, status, headers, b"" if method == "HEAD" else body)

    def request(self, method, url, allow_redirects=True):
        resp = self._one(method, url)
        hops = 0
        while allow_redirects and resp.is_redirect and hops < 10:   # what requests does by default
            url, hops = requests.compat.urljoin(url, resp.headers["Location"]), hops + 1
            resp = self._one(method, url)
        return resp

    def install(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda url, params=None, headers=None, timeout=None, stream=False,
                            allow_redirects=True: self.request("GET", url, allow_redirects))
        monkeypatch.setattr(requests, "head", lambda url, params=None, headers=None, timeout=None,
                            allow_redirects=False: self.request("HEAD", url, allow_redirects))
        monkeypatch.setattr(commons.time, "sleep", lambda s: None)
        return self

    def hosts(self):
        return [re.sub(r"^https?://", "", u).split("/")[0] for _, u in self.seen]


DNS = {"cited.example.org": "93.184.216.34", "images.metmuseum.org": "93.184.216.35",
       "holder.example.net": "93.184.216.36", "upload.wikimedia.org": "198.35.26.112",
       "rebound.example.com": "127.0.0.1", "vm.corp-network.example": "10.20.30.40"}


@pytest.fixture(autouse=True)
def _dns(monkeypatch):
    def getaddrinfo(host, port, *a, **k):
        if host not in DNS:
            raise socket.gaierror("not in the fixture")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (DNS[host], port))]
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


REFUSED = ["http://127.0.0.1/", "https://127.0.0.1/admin", "https://169.254.169.254/latest/meta-data/",
          "https://10.0.0.5/", "https://192.168.1.1/", "https://172.16.4.4/", "https://[::1]/",
          "https://[::ffff:127.0.0.1]/", "https://2130706433/", "https://0x7f.0.0.1/", "https://localhost/",
          "https://intranet/", "https://printer.local/", "https://rebound.example.com/", "https://vm.corp-network.example/",
          "https://cited.example.org:8443/"]


@pytest.mark.parametrize("target", REFUSED)
def test_page_text_does_not_follow_a_redirect_inside(monkeypatch, target):
    """The acceptance: a cited page answering 302 to an internal address gives None, and that address is never
    requested."""
    net = _Net({"https://cited.example.org/a": (302, {"Location": target}, b""),
                target: (200, {"Content-Type": "text/html"}, SECRET)}).install(monkeypatch)
    assert free_evidence.page_text("https://cited.example.org/a") is None
    assert target not in [u for _, u in net.seen]


@pytest.mark.parametrize("target", REFUSED)
def test_page_text_refuses_an_internal_url_outright(monkeypatch, target):
    net = _Net({target: (200, {"Content-Type": "text/html"}, SECRET)}).install(monkeypatch)
    assert free_evidence.page_text(target) is None
    assert net.seen == []


def test_page_text_still_reads_a_public_page_through_a_public_redirect(monkeypatch):
    net = _Net({"https://cited.example.org/a": (301, {"Location": "/b"}, b""),
                "https://cited.example.org/b": (200, {"Content-Type": "text/html; charset=utf-8"}, PAGE)}).install(monkeypatch)
    assert "4,700 tonnes" in free_evidence.page_text("https://cited.example.org/a")
    assert set(net.hosts()) == {"cited.example.org"}


def test_page_text_heads_first_and_skips_what_is_not_a_page(monkeypatch):
    net = _Net({"https://cited.example.org/f.pdf": (200, {"Content-Type": "application/pdf"}, b"%PDF")}).install(monkeypatch)
    assert free_evidence.page_text("https://cited.example.org/f.pdf") is None
    assert net.seen == [("HEAD", "https://cited.example.org/f.pdf")]


def test_page_text_stops_after_too_many_redirects(monkeypatch):
    routes = {f"https://cited.example.org/{i}": (302, {"Location": f"/{i + 1}"}, b"") for i in range(20)}
    net = _Net(routes).install(monkeypatch)
    assert free_evidence.page_text("https://cited.example.org/0") is None
    assert len([m for m, _ in net.seen if m == "GET"]) <= 6


def test_download_jpeg_refuses_an_off_host_museum_url(monkeypatch, tmp_path):
    """The acceptance: a museum candidate whose file is not on its provider's host raises, and nothing is fetched."""
    net = _Net({"https://holder.example.net/x.jpg": (200, {"Content-Type": "image/jpeg"}, _jpeg())}).install(monkeypatch)
    with pytest.raises(ValueError):
        commons.download_jpeg("https://holder.example.net/x.jpg", tmp_path / "x.jpg", provider="met")
    with pytest.raises(ValueError):
        commons.download_jpeg("https://holder.example.net/x.jpg", tmp_path / "x.jpg")   # no provider recorded
    assert net.seen == [] and not (tmp_path / "x.jpg").exists()


@pytest.mark.parametrize("target", ["https://holder.example.net/x.jpg", "https://169.254.169.254/x.jpg",
                                    "http://images.metmuseum.org/x.jpg"])
def test_download_jpeg_refuses_a_redirect_off_its_provider(monkeypatch, tmp_path, target):
    net = _Net({"https://images.metmuseum.org/a.jpg": (302, {"Location": target}, b""),
                target: (200, {"Content-Type": "image/jpeg"}, _jpeg())}).install(monkeypatch)
    with pytest.raises(ValueError):
        commons.download_jpeg("https://images.metmuseum.org/a.jpg", tmp_path / "x.jpg", provider="met")
    assert target not in [u for _, u in net.seen]


def test_download_jpeg_fetches_from_the_providers_host(monkeypatch, tmp_path):
    net = _Net({"https://images.metmuseum.org/a.jpg": (302, {"Location": "/b.jpg"}, b""),
                "https://images.metmuseum.org/b.jpg": (200, {"Content-Type": "image/jpeg"}, _jpeg()),
                "https://upload.wikimedia.org/c.jpg": (200, {"Content-Type": "image/jpeg"}, _jpeg())}).install(monkeypatch)
    commons.download_jpeg("https://images.metmuseum.org/a.jpg", tmp_path / "x.jpg", provider="met")
    commons.download_jpeg("https://upload.wikimedia.org/c.jpg", tmp_path / "y.jpg")   # a Commons result: no provider
    assert Image.open(tmp_path / "x.jpg").size == Image.open(tmp_path / "y.jpg").size == (900, 700)
    assert set(net.hosts()) == {"images.metmuseum.org", "upload.wikimedia.org"}


def test_europeana_holder_host_is_resolved_and_refused_inside(monkeypatch, tmp_path):
    """Europeana's holders serve their own files (any host): a holder name pointing inside is refused."""
    net = _Net({"https://rebound.example.com/x.jpg": (200, {"Content-Type": "image/jpeg"}, _jpeg())}).install(monkeypatch)
    with pytest.raises(ValueError):
        commons.download_jpeg("https://rebound.example.com/x.jpg", tmp_path / "x.jpg", provider="europeana")
    with pytest.raises(ValueError):
        footage.download("https://rebound.example.com/x.webm", "europeana", tmp_path / "f")
    assert net.seen == []


def test_reference_download_refuses_off_host_and_inside(monkeypatch, tmp_path):
    net = _Net({"https://holder.example.net/x.jpg": (200, {"Content-Type": "image/jpeg"}, _jpeg()),
                "https://images.metmuseum.org/a.jpg": (302, {"Location": "https://10.0.0.5/x.jpg"}, b""),
                "https://10.0.0.5/x.jpg": (200, {"Content-Type": "image/jpeg"}, _jpeg())}).install(monkeypatch)
    assert image_gen._download_reference({"download_url": "https://holder.example.net/x.jpg", "provider": "met"},
                                         tmp_path) is None
    assert image_gen._download_reference({"download_url": "https://holder.example.net/x.jpg"}, tmp_path) is None
    assert image_gen._download_reference({"download_url": "https://images.metmuseum.org/a.jpg", "provider": "met"},
                                         tmp_path) is None
    assert [u for _, u in net.seen] == ["https://images.metmuseum.org/a.jpg"]


def test_every_museum_provider_has_its_hosts():
    """Every provider a museum search records is routed through footage's host allowlist."""
    source = Path(__file__).resolve().parents[1].joinpath("src", "museum_sources.py").read_text(encoding="utf-8")
    providers = set(re.findall(r'provider="([a-z]+)"', source))
    assert providers and providers <= set(footage.ALLOWED_HOSTS)
    assert providers <= set(footage.IMAGE_PROVIDERS)
