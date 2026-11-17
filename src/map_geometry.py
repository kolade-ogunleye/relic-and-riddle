"""GEOS geometry and PROJ projections for map ground, clipping and placement."""

import math
from functools import lru_cache

from pyproj import Geod, Proj
from shapely import (
    GeometryCollection,
    LineString,
    Point,
    Polygon,
    box,
    make_valid,
    maximum_inscribed_circle,
    union_all,
)
from shapely.ops import nearest_points

GEODESIC = Geod(ellps="WGS84")
METRES_PER_ARC_DEGREE = math.pi * 6371008.8 / 180.0


def polygon_parts(geometry):
    """Only areal components, including repaired polygons and their holes."""
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    return [part for child in getattr(geometry, "geoms", ()) for part in polygon_parts(child)]


@lru_cache(maxsize=512)
def _polygon(rings):
    if not rings or len(rings[0]) < 3:
        return GeometryCollection()
    polygon = Polygon(rings[0], [hole for hole in rings[1:] if len(hole) >= 3])
    return polygon if polygon.is_valid else make_valid(polygon)


def polygon(rings):
    return _polygon(tuple(tuple((float(x), float(y)) for x, y in ring) for ring in rings))


def contains(x, y, rings):
    """Boundary points belong to the ground; holes remain outside it."""
    return bool(polygon(rings).covers(Point(x, y)))


def resolvable_hole(ring, pixel_size=1.0):
    """Whether a water hole has a one-pixel-wide interior at the drawing scale.

    Separate source layers can leave subpixel slivers along a shared boundary.
    Rasterizing those as opaque one-pixel cuts creates a false coast. This test
    affects drawing only; the source geometry and its actual outer coast stay intact.
    """
    if not ring:
        return False
    xs, ys = zip(*ring)
    # No inset of radius half a pixel can survive in a box a pixel wide.
    # Avoid building and buffering GEOS polygons for that exact empty case.
    if max(xs) - min(xs) <= pixel_size or max(ys) - min(ys) <= pixel_size:
        return False
    return not polygon([ring]).buffer(-0.5 * pixel_size).is_empty


def even_odd(rings):
    result = GeometryCollection()
    for ring in rings:
        if len(ring) >= 3:
            result = result.symmetric_difference(polygon([ring]))
    return result


def rings_of(geometry):
    return [[list(part.exterior.coords)] + [list(hole.coords) for hole in part.interiors]
            for part in polygon_parts(geometry)]


def union_polygons(polygons):
    return rings_of(union_all([polygon(rings) for rings in polygons]))


def intersect_polygons(first, second):
    """Ground supported by both independent representations of a boundary."""
    return rings_of(union_all([polygon(rings) for rings in first]).intersection(
        union_all([polygon(rings) for rings in second])))


def clip_rings(ring, bounds):
    """All disconnected pieces of a clipped ring, without artificial joining edges."""
    clipped = polygon([ring]).intersection(box(*bounds))
    return [list(part.exterior.coords) for part in polygon_parts(clipped)]


def pole(rings, precision=1.0, avoid=(), avoid_circles=()):
    ground = even_odd(rings)
    obstacles = [box(*bounds) for bounds in avoid]
    for x, y, radius in avoid_circles:
        if radius > 0:
            # Circumscribed buffer: a polygonal obstacle never underestimates the
            # circle being protected, including between its approximation vertices.
            obstacles.append(Point(x, y).buffer(radius / math.cos(math.pi / 128), quad_segs=32))
    if obstacles:
        ground = ground.difference(union_all(obstacles))
    parts = polygon_parts(ground)
    if not parts:
        return None
    area = union_all(parts)
    axis = maximum_inscribed_circle(area, tolerance=max(1e-6, precision))
    if axis.is_empty or axis.length <= 0:
        return None
    centre = axis.coords[0]
    return float(centre[0]), float(centre[1]), float(axis.length)


@lru_cache(maxsize=128)
def projection(lon0, lat0, mode="aeqd"):
    """Local WGS84 projection, bundled PROJ data only; no transformation grids."""
    return Proj(proj=mode, lon_0=lon0, lat_0=lat0, ellps="WGS84", units="m")


@lru_cache(maxsize=131072)
def azimuthal(lon, lat, lon0, lat0):
    # Framing tries many scales of the same site-centred geometry. Only the
    # pixel scale changes, so these exact PROJ coordinates can be reused.
    x, y = projection(lon0, lat0)(lon, lat)
    return x / METRES_PER_ARC_DEGREE, y / METRES_PER_ARC_DEGREE


def azimuthal_many(lons, lats, lon0, lat0):
    """azimuthal() over arrays in one PROJ call (the same per-point arithmetic)."""
    x, y = projection(lon0, lat0)(lons, lats)
    return x / METRES_PER_ARC_DEGREE, y / METRES_PER_ARC_DEGREE


def azimuthal_inverse(x, y, lon0, lat0):
    return projection(lon0, lat0)(x * METRES_PER_ARC_DEGREE, y * METRES_PER_ARC_DEGREE, inverse=True)


def orthographic(lon, lat, lon0, lat0):
    if lon == lon0 and lat == lat0:
        return 0.0, 0.0, True
    x, y = projection(lon0, lat0, "ortho")(lon, lat)
    if not math.isfinite(x) or not math.isfinite(y):
        return 0.0, 0.0, False
    return x / GEODESIC.a, y / GEODESIC.a, True


def nearest_on_ring(lon, lat, ring):
    """Closest point on the actual boundary in a site-centred metric projection."""
    if len(ring) < 2:
        return math.inf, (lon, lat)
    project = projection(lon, lat)
    xs, ys = project(*zip(*ring))
    points = list(zip(xs, ys))
    if points[0] != points[-1]:
        points.append(points[0])
    if not all(math.isfinite(value) for point in points for value in point):
        return math.inf, (lon, lat)
    boundary = LineString(points)
    closest = nearest_points(Point(0, 0), boundary)[1]
    return float(closest.distance(Point(0, 0))) / 1000, project(closest.x, closest.y, inverse=True)


def route(a, b, steps=32):
    if steps < 1:
        raise ValueError("A route requires at least one step")
    if a == b:
        return [a] * (steps + 1)
    return [a, *GEODESIC.npts(*a, *b, steps - 1), b] if steps > 1 else [a, b]


def meridian_km_per_degree(lat):
    lo, hi = max(-90.0, lat - 0.0005), min(90.0, lat + 0.0005)
    return GEODESIC.inv(0, lo, 0, hi)[2] / (hi - lo) / 1000
