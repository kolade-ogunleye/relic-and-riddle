"""Design handover (2026-11-04): the long 9:16 myth card keeps its source line. The fit tries, in order, the source
at MYTH_FLOOR_916 or more, then with one more fact line (MYTH_EXTRA_LINE), then down to MYTH_FLOOR_SOURCE (76 px),
then at the largest size that holds it, each rung with no lone word first; a myth the source cannot fit is refused,
never drawn without it. 16:9 and myths without a host are unchanged."""
import pytest

import src.design_cards as dc

M = "EXTRATERRESTRIALS CONSTRUCTED THE"
# fact lengths whose source line HEAD 5fdb5dc dropped ("no room": 92 / 88 px without it)
DROPPED = ["WIND AND WATER SLOWLY ERODED THE RAISED DOME OVER MILLIONS",
           "WIND AND WATER SLOWLY ERODED THE RAISED DOME OVER MILLIONS OF",
           "WIND AND WATER SLOWLY ERODED THE RAISED DOME OVER MILLIONS OF YEARS",
           "WIND AND WATER SLOWLY ERODED THE RAISED DOME OVER MILLIONS OF YEARS INTO"]
TALL, WIDE = dc._Geo(1080, 1920), dc._Geo(1920, 1080)


def test_floors():
    assert (dc.MYTH_FLOOR_916, dc.MYTH_FLOOR_SOURCE, dc.MYTH_EXTRA_LINE) == (84, 76, 1)


@pytest.mark.parametrize("fact", DROPPED)
def test_long_916_myth_keeps_its_source(fact):
    P, m_lines, f_lines, host, why, *_ = dc._myth_fit(M, fact, "BRITANNICA.COM", TALL)   # merge: + myth px, compact
    assert host == "BRITANNICA.COM" and why is None
    assert P >= dc.MYTH_FLOOR_SOURCE
    assert len(f_lines) <= dc.MYTH_LINES[False][1] + dc.MYTH_EXTRA_LINE


def test_never_drawn_without_its_source():
    """Over a sweep of lengths, a 9:16 myth with a host either keeps it or is refused."""
    words = "WIND AND WATER SLOWLY ERODED THE RAISED DOME OVER MILLIONS OF YEARS INTO ITS CONCENTRIC RINGS".split()
    for k in range(4, len(words) + 1):
        fit = dc._myth_fit(M, " ".join(words[:k]), "BRITANNICA.COM", TALL)
        assert fit is None or (fit[3] == "BRITANNICA.COM" and fit[4] is None), (k, fit)


def test_without_a_host_and_16x9_unchanged():
    bare = dc._myth_fit(M, DROPPED[0], None, TALL)
    assert bare is not None and bare[3] is None and bare[4] is None
    wide = dc._myth_fit(M, DROPPED[0], "BRITANNICA.COM", WIDE)
    assert wide is not None and wide[3] == "BRITANNICA.COM"
