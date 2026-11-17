"""Reviewer r11rev1 independent name checker. Raw Natural Earth geojson, own ray-cast point-in-polygon, own
denser sampling of every drawn name box (affine fit of the layout's pixel->lonlat pairs, then an 11x5 grid
plus corners), own home truth (smallest 1:10m map-subunit polygon holding the site, else nearest within 50 km),
own water truth (smallest marine polygon; North/South by the equator for the two oceans).
usage: mychk.py layout.json [...]"""
import json, math, re, sys, unicodedata
from functools import lru_cache
import numpy as np

G = "/home/user/relic-and-riddle/tools/geo/"


def fold(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", "and").replace("saint ", "st ").replace("st. ", "st ")
    return re.sub(r"[^a-z]", "", s)


def pip(x, y, ring):
    ins = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-300) + xi:
            ins = not ins
        j = i
    return ins


def area(r):
    return abs(sum(r[i][0] * r[(i + 1) % len(r)][1] - r[(i + 1) % len(r)][0] * r[i][1] for i in range(len(r)))) / 2


def seg_km(px, py, ax, ay, bx, by, lat):
    c = math.cos(math.radians(lat))
    ax, bx, px_ = ax * c, bx * c, px * c
    dx, dy = bx - ax, by - ay
    t = 0 if dx == dy == 0 else max(0, min(1, ((px_ - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px_ - (ax + t * dx), py - (ay + t * dy)) * 111.2


class F:
    def __init__(self, p, g):
        self.p = p
        self.polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        self.pe = [(min(c[0] for c in q[0]), min(c[1] for c in q[0]), max(c[0] for c in q[0]), max(c[1] for c in q[0]))
                   for q in self.polys]

    def hold_poly(self, x, y):
        for q, e in zip(self.polys, self.pe):
            if e[0] <= x <= e[2] and e[1] <= y <= e[3] and pip(x, y, q[0]) and not any(pip(x, y, h) for h in q[1:]):
                return q
        return None

    def km(self, x, y):
        best = math.inf
        for q, e in zip(self.polys, self.pe):
            if not (e[0] - 1 <= x <= e[2] + 1 and e[1] - 1 <= y <= e[3] + 1):
                continue
            r = q[0]
            for i in range(len(r) - 1):
                best = min(best, seg_km(x, y, r[i][0], r[i][1], r[i + 1][0], r[i + 1][1], y))
        return best

    def names(self):
        return {fold(self.p.get(k)) for k in ("NAME", "NAME_LONG", "ADMIN", "GEOUNIT", "SUBUNIT", "NAME_EN",
                                                "FORMAL_EN", "BRK_NAME", "NAME_SORT", "SOVEREIGNT") if self.p.get(k)}


@lru_cache(None)
def load(fn):
    d = json.load(open(G + fn))
    return [F(f["properties"], f["geometry"]) for f in d["features"]
            if f.get("geometry") and f["geometry"]["type"] in ("Polygon", "MultiPolygon")]


def W(lo):
    return (lo + 180.0) % 360.0 - 180.0


def subunits_at(lo, la):
    lo = W(lo)
    out = []
    for f in load("ne_10m_admin_0_map_subunits.geojson"):
        q = f.hold_poly(lo, la)
        if q is not None:
            out.append((area(q[0]), f))
    return [f for _, f in sorted(out, key=lambda t: t[0])]


def ice_at(lo, la):
    return la < -55 and any(f.hold_poly(W(lo), la) is not None for f in load("ne_50m_antarctic_ice_shelves_polys.geojson"))


def water(lo, la):
    lo = W(lo)
    best = None
    for f in load("ne_10m_geography_marine_polys.geojson"):
        q = f.hold_poly(lo, la)
        if q is not None:
            a = area(q[0])
            if best is None or a < best[0]:
                best = (a, f)
    if best is None:
        return None
    f = best[1]
    nm = (f.p.get("name") if f.p.get("featurecla") == "ocean" else (f.p.get("name_en") or f.p.get("name"))) or ""
    u = nm.upper()
    for oc in ("ATLANTIC", "PACIFIC"):
        if oc in u and (u.startswith("NORTH") or u.startswith("SOUTH")):
            u = ("NORTH " if la >= 0 else "SOUTH ") + oc + " OCEAN"
    return u


def home_truth(lo, la):
    s = subunits_at(lo, la)
    if s:
        return s[0], 0.0
    best = (math.inf, None)
    for f in load("ne_10m_admin_0_map_subunits.geojson"):
        d = f.km(W(lo), la)
        if d < best[0]:
            best = (d, f)
    return (best[1], best[0]) if best[0] <= 50 else (None, best[0])


def ents_named(t):
    t = fold(t)
    out = []
    for fn in ("ne_10m_admin_0_map_subunits.geojson", "ne_10m_admin_0_countries.geojson"):
        for f in load(fn):
            if t in f.names():
                out.append(f)
    # an entity's text may be the ADMIN of a multi-subunit country: all subunits with that ADMIN/GEOUNIT/SOV
    return out


def on(ents, lo, la, tol=0.0):
    lo = W(lo)
    if any(f.hold_poly(lo, la) is not None for f in ents):
        return True
    return tol > 0 and any(f.km(lo, la) <= tol for f in ents)


def grid(box, nx=11, ny=5, inset=2.0):
    x0, y0, x1, y1 = box
    x0, x1 = (x0 + inset, x1 - inset) if x1 - x0 > 2 * inset else ((x0 + x1) / 2,) * 2
    y0, y1 = (y0 + inset, y1 - inset) if y1 - y0 > 2 * inset else ((y0 + y1) / 2,) * 2
    pts = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
    pts += [(x0 + (x1 - x0) * (i + .5) / nx, y0 + (y1 - y0) * (j + .5) / ny) for j in range(ny) for i in range(nx)]
    return pts


def renderer_points(box):
    # mirror of the renderer's sample layout is unknown; fit an affine on whatever pairs it gave
    return None


def affine(box, lonlat):
    n = len(lonlat)
    # the renderer's own points are its grid; we only use them to fit pixel->lonlat; reconstruct pixel coords by
    # trying the chk.py 7x3 layout first, then a least-squares over the box corners
    x0, y0, x1, y1 = box
    for nx, ny in ((7, 3), (5, 3), (9, 3), (7, 2), (5, 2), (3, 2), (11, 3)):
        if 4 + nx * ny == n:
            px = grid(box, nx, ny)
            break
    else:
        return None
    ll = np.array(lonlat, float)
    ll[:, 0] = np.unwrap(np.radians(ll[:, 0])) * 180 / math.pi
    A = np.c_[np.array(px), np.ones(n)]
    if np.linalg.matrix_rank(A) < 3:
        return None
    cf, res, *_ = np.linalg.lstsq(A, ll, rcond=None)
    return cf


def check(path):
    lay = json.load(open(path))
    out, notes = [], []
    site = lay.get("anchor_lonlat")
    route = "stops" in lay or lay.get("kind") == "route" or site is None
    ctext = (lay.get("country") or {}).get("text")
    if not route and site:
        f, d = home_truth(*site)
        if f is None:
            if ctext:
                out.append(f"HOME '{ctext}' named but no land within 50 km of the site")
        elif not ctext:
            notes.append(f"no home name; truth {f.p.get('NAME_LONG')} ({f.p.get('ADMIN')}) d={d:.1f}km; drop='{lay.get('home_drop')}'")
        else:
            ok_names = f.names()
            for g in load("ne_10m_admin_0_map_subunits.geojson"):
                if g.p.get("ADMIN") == f.p.get("ADMIN") and fold(g.p.get("ADMIN")) in {fold(f.p.get("ADMIN"))}:
                    if g.p.get("GEOUNIT") == f.p.get("GEOUNIT"):
                        ok_names |= g.names() - {fold(g.p.get("SOVEREIGNT"))} | {fold(f.p.get("ADMIN"))}
            # the admin's other subunits too (UNITED KINGDOM for Scotland)
            if fold(ctext) not in ok_names:
                out.append(f"HOME '{ctext}' but site ground is {f.p.get('NAME_LONG')} / {f.p.get('GEOUNIT')} / {f.p.get('ADMIN')} (d={d:.1f} km)")
            elif d > 0:
                notes.append(f"site {d:.2f} km off its ground {f.p.get('NAME_LONG')}")
    drawn = {c.upper() for c in lay.get("water", [])} | set(lay.get("neighbours", [])) | ({ctext} if ctext else set())
    for c in lay.get("name_checks", []):
        if c["fault"]:
            notes.append(f"dropped {c['kind']} {c['text']}: {c['fault'][:70]}")
            continue
        if c["text"] not in drawn:
            continue
        cf = affine(c["box"], c["lonlat"])
        pts = c["lonlat"] if cf is None else [tuple(np.array([x, y, 1.0]) @ cf) for x, y in grid(c["box"])]
        txt, k = c["text"], c["kind"]
        if k == "water":
            bad = []
            for lo, la in pts:
                s = subunits_at(lo, la)
                if s or ice_at(lo, la):
                    bad.append(f"land {s[0].p.get('NAME') if s else 'ice'} @{lo:.3f},{la:.3f}")
                else:
                    u = water(lo, la)
                    if u != txt:
                        bad.append(f"{u} @{lo:.2f},{la:.2f}")
            if bad:
                out.append(f"WATER {txt}: {len(bad)}/{len(pts)} off: {bad[:3]}")
        elif k == "home":
            ents = ents_named(txt) + (ents_named(lay["home"]) if lay.get("home") else [])
            if not ents:
                out.append(f"HOME {txt}: no entity of that name in raw data")
                continue
            inside = (lay.get("country") or {}).get("inside", True)
            if inside:
                offs = [(lo, la) for lo, la in pts if not on(ents, lo, la, 2.0)]
                if offs:
                    where = [(subunits_at(*p)[:1] or [None])[0] for p in offs]
                    out.append(f"HOME {txt} inside: {len(offs)}/{len(pts)} off its ground: {[w.p.get('NAME') if w else 'water' for w in where[:4]]}")
            else:
                oth = [(p, subunits_at(*p)) for p in pts]
                oth = [(p, s) for p, s in oth if s and not on(ents, *p)]
                if oth:
                    out.append(f"HOME {txt} outside over other land {oth[0][1][0].p.get('NAME')} ({len(oth)}/{len(pts)})")
                if not (lay.get("country") or {}).get("leader"):
                    notes.append(f"home {txt} outside without leader")
        else:
            ents = ents_named(txt)
            if not ents:
                out.append(f"{k.upper()} {txt}: no entity of that name in raw data")
                continue
            offs = [p for p in pts if not on(ents, *p, 2.0)]
            land_offs = [(p, subunits_at(*p)) for p in offs]
            land_offs = [(p, s) for p, s in land_offs if s]
            if land_offs:
                out.append(f"{k.upper()} {txt}: {len(land_offs)}/{len(pts)} pts over other land {land_offs[0][1][0].p.get('NAME')}")
            if offs and len(offs) > len(land_offs):
                notes.append(f"{k} {txt}: {len(offs) - len(land_offs)}/{len(pts)} pts over water (leader? {bool(c.get('leader'))})")
    return lay, out, notes


if __name__ == "__main__":
    for p in sys.argv[1:]:
        nm = p.split("/")[-1].replace(".mp4.layout.json", "")
        try:
            lay, out, notes = check(p)
        except Exception as e:
            import traceback; traceback.print_exc()
            print(nm, "ERR", repr(e)); continue
        print(f"{nm}: home={lay.get('home')} country={(lay.get('country') or {}).get('text')} inside={(lay.get('country') or {}).get('inside')} water={lay.get('water')} nb={lay.get('neighbours')}")
        for n in notes:
            print("   ..", n)
        for o in out:
            print("   !!", o)
