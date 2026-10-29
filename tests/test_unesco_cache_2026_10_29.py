"""A failed UNESCO download wrote an empty stub (<query><row></row></query>) to the real cache, which was then trusted
as fresh for CACHE_TTL_DAYS: on production one failed download would have left discovery with no sites for 30 days,
and in the suite the stub un-skipped the real-list tests on the next run (2026-10-29). Only a real dataset (parses,
lists a site) is now written or trusted; a failure leaves nothing on disk and is retried after a short in-memory
back-off; and the suite's UNESCO cache is a scratch path unless a test reads the real list."""
import os
import time

import pytest

from src import config, unesco
from src.unesco import UNESCOEngine, has_dataset

SITE = (b"<query><row><id_number>91</id_number><site>Historic Centre of Rome</site><states>Italy</states>"
        b"<category>Cultural</category></row></query>")
STUB = "<query><row></row></query>"


class _Reply:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


@pytest.fixture
def downloads(monkeypatch):
    calls = []

    def serve(result):
        def get(url, **kwargs):
            calls.append(url)
            if isinstance(result, Exception):
                raise result
            return _Reply(result)
        monkeypatch.setattr(unesco.requests, "get", get)
    return calls, serve


def test_a_failed_download_with_no_cache_writes_nothing_and_is_retried(tmp_path, downloads, monkeypatch):
    calls, serve = downloads
    cache = tmp_path / "unesco" / "sites.xml"
    serve(OSError("blocked"))
    assert UNESCOEngine(cache).load_sites() == {} and not cache.exists()
    assert UNESCOEngine(cache).load_sites() == {} and len(calls) == 1            # backed off, in memory only
    monkeypatch.setattr(unesco, "DOWNLOAD_RETRY_SECONDS", 0)
    serve(SITE)
    sites = UNESCOEngine(cache).load_sites()
    assert list(sites) == [91] and len(calls) == 2 and has_dataset(cache)
    assert not cache.with_name("sites.xml.partial").exists()


def test_the_same_engine_tries_again_after_a_failure(tmp_path, downloads, monkeypatch):
    calls, serve = downloads
    monkeypatch.setattr(unesco, "DOWNLOAD_RETRY_SECONDS", 0)
    engine = UNESCOEngine(tmp_path / "sites.xml")
    serve(OSError("blocked"))
    assert engine.load_sites() == {}
    serve(SITE)
    assert list(engine.load_sites()) == [91] and len(calls) == 2


def test_an_empty_stub_is_never_trusted_and_an_empty_download_never_written(tmp_path, downloads):
    calls, serve = downloads
    cache = tmp_path / "sites.xml"
    cache.write_text(STUB, encoding="utf-8")                                    # fresh, but no site in it
    assert not has_dataset(cache)
    serve(STUB.encode())
    assert UNESCOEngine(cache).load_sites() == {} and len(calls) == 1
    assert cache.read_text(encoding="utf-8") == STUB                             # left as found, never trusted
    assert not has_dataset(tmp_path / "missing.xml")
    (tmp_path / "broken.xml").write_text("<query><row>", encoding="utf-8")
    assert not has_dataset(tmp_path / "broken.xml")


def test_a_stale_dataset_is_used_when_the_download_fails(tmp_path, downloads):
    calls, serve = downloads
    cache = tmp_path / "sites.xml"
    cache.write_bytes(SITE)
    old = time.time() - (unesco.CACHE_TTL_DAYS + 1) * 86400
    os.utime(cache, (old, old))
    serve(OSError("blocked"))
    assert list(UNESCOEngine(cache).load_sites()) == [91] and len(calls) == 1
    assert cache.read_bytes() == SITE


def test_a_fresh_dataset_is_not_downloaded_again(tmp_path, downloads):
    calls, serve = downloads
    cache = tmp_path / "sites.xml"
    cache.write_bytes(SITE)
    serve(OSError("must not be called"))
    assert list(UNESCOEngine(cache).load_sites()) == [91] and not calls


def test_the_suite_never_uses_the_real_cache_by_default():
    real = config.BASE_DIR / "cache" / "unesco" / "sites.xml"
    assert unesco.CACHE_FILE != real and UNESCOEngine().cache_file == unesco.CACHE_FILE
    assert unesco._failed_downloads == {}


@pytest.mark.real_unesco
def test_a_test_marked_real_unesco_reads_the_real_cache():
    assert unesco.CACHE_FILE == config.BASE_DIR / "cache" / "unesco" / "sites.xml"
