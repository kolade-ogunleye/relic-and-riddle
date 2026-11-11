"""Five-event timelines retain dates and words inside the existing safe box."""

from functools import lru_cache
from itertools import pairwise, product
from math import ceil

from PIL import Image, ImageDraw


def plan(events, width, height):
    key = tuple((int(event["year"]), str(event["label"]), bool(event.get("approximate"))) for event in events)
    return _plan(key, width, height)


@lru_cache(maxsize=256)
def _plan(key, width, height):
    from . import graphics as g
    from .typography import get_font

    events = [{"year": year, "label": label, "approximate": approximate} for year, label, approximate in key]
    box = g.safe_zones.graphics_box(width, height)
    spine = g.inset_x0(width, height) + round(16 * width / g.VIDEO_WIDTH)
    left = spine + round(56 * width / g.VIDEO_WIDTH)
    font = get_font("Montserrat", g.TALL_LABEL_MIN_PX, weight=g.TALL_LABEL_WEIGHT)
    gap_font = get_font("Montserrat", g.SPAN_MIN_PX, weight=g.TALL_SPAN_WEIGHT)
    label_cap, gap_cap = g.cap_height(font), g.cap_height(gap_font)
    track, pitch = g.tall_track(font.size), round(font.size * g.TALL_LEADING)
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    spans = [g.gap_text(a, b) for a, b in pairwise(events)]
    fitted = {}

    def fit(text, measure):
        cache_key = (text, measure)
        if cache_key in fitted:
            return fitted[cache_key]
        for rows in range(1, g.TALL_LABEL_ROWS_MAX + 1):
            lines, _font, short = g._probe_tracked(probe, text.upper(), "Montserrat", font.size,
                                                  measure - g.TALL_INK_RESERVE, rows, font.size, track,
                                                  weight=g.TALL_LABEL_WEIGHT, quiet=True)
            if short:
                continue
            if len(lines) > 1:
                lines, _obeyed = g.balanced_label_rows(lines, font, measure - g.TALL_INK_RESERVE,
                                                     track, source=text)
            if not g.label_hard_fault(lines, text):
                fitted[cache_key] = lines
                return lines
        fitted[cache_key] = None
        return None

    # Preserve the existing year/label ratio, label and span size floors. A
    # date column buys room by putting short labels beside their date. A long
    # proper name may still take the whole measure underneath its date.
    best = None
    best_rank = None
    for year_px in range(128, 1, -1):
        year_font = get_font("Anton", year_px)
        cap = g.cap_height(year_font)
        if cap < g.TALL_YEAR_RATIO_LAST * label_cap:
            break
        year_width = max(g._union(g.year_ink_boxes(0, cap, event["year"], event["approximate"], year_font))[2]
                         for event in events)
        label_left = left + year_width + 32
        choices = []
        for event in events:
            candidates = []
            for inline, measure in ((True, box[2] - label_left), (False, box[2] - left)):
                if measure <= 0:
                    continue
                lines = fit(event["label"], measure)
                if lines is None:
                    continue
                label_height = label_cap + pitch * (len(lines) - 1) + g.span_descent(lines[-1], font) + 2
                block_height = max(cap + 2, label_height) if inline else cap + g.TALL_YEAR_LABEL_AIR + label_height
                candidates.append((block_height, not inline, lines))
            if not candidates:
                break
            choices.append(candidates)
        if len(choices) != len(events):
            continue
        # The date column's vertical inner air remains the existing 14 px;
        # its horizontal date/label gutter does not consume the span's band.
        above, below = g.span_air_rule(font.size, gap_font, g.TALL_YEAR_LABEL_AIR, False)
        # A close date can have no printed span, but it remains a separate
        # entry. Apply the same hierarchy and ink-overshoot allowance as the
        # ordinary portrait planner rather than relying on its 32 px band.
        inner = max(g.TALL_YEAR_LABEL_AIR, pitch - label_cap)
        least = ceil(g.TALL_ENTRY_SEPARATION * (inner + 1)) + g.SPAN_OVERSHOOT
        bands = [max(least, above + gap_cap + below + g.span_descent(text, gap_font)
                     if text else g.TALL_NO_SPAN_AIR) for text in spans]
        # The shortest cell can force three narrow rows even when a whole
        # column of two-row labels fits. Compare complete arrangements and pay
        # for a wider measure with the year heading before adding text rows.
        # Font/air floors stay fixed; the search has at most 2**5 arrangements.
        for blocks in product(*choices):
            rank = (sum(max(0, len(block[2]) - 2) for block in blocks), -year_px,
                    sum(block[0] for block in blocks))
            if best_rank is not None and rank >= best_rank:
                continue
            needed = sum(block[0] for block in blocks) + sum(bands)
            if needed > box[3] - box[1] - g.TALL_TOP_PAD:
                continue
            first = box[1] + g.TALL_TOP_PAD
            last = box[3] - blocks[-1][0]
            needs = [block[0] + band for block, band in zip(blocks, bands)]
            spacing = g.timeline_positions(events, first, last, needs)
            if any(b - a < need - 1 for a, b, need in zip(spacing.positions, spacing.positions[1:], needs)):
                continue
            best_rank = rank
            best = {"spine": spine, "left": left, "label_left": label_left, "font": font, "year_font": year_font,
                    "gap_font": gap_font, "cap": cap, "label_cap": label_cap, "track": track, "pitch": pitch,
                    "blocks": blocks, "spans": spans, "spacing": spacing, "above": above, "below": below}
        if best_rank is not None and best_rank[0] == 0:
            return best
    return best


def fits_both_formats(events):
    key = tuple((int(event["year"]), str(event["label"]), bool(event.get("approximate"))) for event in events)
    return _fits_both_formats(key)


@lru_cache(maxsize=256)
def _fits_both_formats(key):
    """A fifth event is extra capacity only when both formats keep their floors.

    The existing four-event significance policy remains the fallback for copy
    that cannot occupy five readable cells. This never drops a close date just
    because another gap is longer.
    """
    from itertools import combinations, pairwise

    from . import graphics as g
    from .typography import get_font

    events = [{"year": year, "label": label, "approximate": approximate} for year, label, approximate in key]
    if plan(events, 1080, 1920) is None:
        return False
    label = get_font("Montserrat", 34, weight=800)
    year = get_font("Anton", 72)
    span_font = get_font("Montserrat", g.WIDE_SPAN_MIN_PX, weight=700)
    widths = []
    year_widths = []
    for event in events:
        words = event["label"].upper().split()
        if not words or len(words) > 14:
            return False
        best = None
        for rows in range(1, min(3, len(words)) + 1):
            for cuts in combinations(range(1, len(words)), rows - 1):
                bounds = (0, *cuts, len(words))
                lines = [" ".join(words[a:b]) for a, b in pairwise(bounds)]
                if g.label_hard_fault(lines, event["label"]):
                    continue
                line_widths = [g._tracked_w(line, label, 2) for line in lines]
                widest = max(line_widths)
                if rows > 1 and len(lines[0].split()) == 1 and line_widths[0] < g.WIDE_LONE_SHARE * widest:
                    continue
                if best is None or widest < best:
                    best = widest
        if best is None:
            return False
        boxes = g.year_ink_boxes(22, 200, int(event["year"]), bool(event.get("approximate")), year)
        year_width = max(box[2] for box in boxes)
        year_widths.append(year_width)
        widths.append(max(year_width, best + 22 + g.TALL_INK_RESERVE))
    available = g.WIDE_TIMELINE[1] - g.WIDE_TIMELINE[0]
    if sum(widths[::2]) + 48 > available or sum(widths[1::2]) + 24 > available:
        return False
    needs = []
    for index, (earlier, later) in enumerate(pairwise(events)):
        text = g.gap_text(earlier, later)
        if not text:
            needs.append(48)
            continue
        width = max(g.tracked_width(line, span_font, 1) for line in g.wide_span_lines(text, True))
        need = max(140, width + 60)
        if index % 2:
            need = max(need, year_widths[index] + width + g.WIDE_SPAN_OFF + 4)
        needs.append(need)
    if sum(needs) > available:
        return False
    return g._render_timeline_wide(events, None, 1920, 1080, None, _capacity_only=True)


def render(events, out, width, height, photo, layout):
    from . import graphics as g

    font, year_font, gap_font = layout["font"], layout["year_font"], layout["gap_font"]
    cap, left, track = layout["cap"], layout["left"], layout["track"]
    spacing = layout["spacing"]
    ground = g.timeline_backdrop(photo, width, height)
    entries, plates, gaps = [], [], []
    for index, (event, block, top) in enumerate(zip(events, layout["blocks"], spacing.positions)):
        block_height, stacked, lines = block
        if len(lines) > g.tall_label_rows_budget(len(events)):
            g.log.error("Timeline copy-fit: label %r needs %d rows of its date-column measure; set whole.",
                        event["label"].upper(), len(lines))
        if any(line.split()[-1].strip(",;:.") in g.TALL_STOP_WORDS for line in lines[:-1]):
            g.log.info("Timeline label %r: no break of its %d rows avoids a short word at a row's end; set balanced.",
                       event["label"].upper(), len(lines))
        baseline = top + cap
        boxes = g.year_ink_boxes(left, baseline, int(event["year"]), bool(event.get("approximate")), year_font)
        year_plates = g._year_plates(ground, left + left - boxes[0][0], baseline, int(event["year"]),
                                    bool(event.get("approximate")), year_font, g.GOLD)
        lx = left if stacked else layout["label_left"]
        ly = baseline + g.TALL_YEAR_LABEL_AIR + layout["label_cap"] if stacked else top + layout["label_cap"]
        label_plates = []
        for row, line in enumerate(lines):
            row_y = ly + row * layout["pitch"]
            label_plates.append(g.plan_text(ground, (g.tall_left_x(lx, row_y, line, font, track), row_y),
                                            line, font, g.TALL_LABEL_FILL, "ls", track=track))
        plates.extend((f"year{index}" if part == len(year_plates) - 1 else f"year{index}_circa", plate)
                      for part, plate in enumerate(year_plates))
        plates.extend((f"label{index}", plate) for plate in label_plates)
        originals = [*year_plates, *label_plates]
        twins = [g._twin(ground, plate, g.brand.STONE) for plate in year_plates]
        twins += [g._twin(ground, plate, g.brand.STONE_LT) for plate in label_plates]
        entries.append((top + cap / 2, originals, twins))
        if index >= len(layout["spans"]) or not layout["spans"][index]:
            continue
        text = layout["spans"][index]
        bottom = top + block_height
        next_top = spacing.positions[index + 1]
        descent = g.span_descent(text, gap_font)
        slack = next_top - bottom - layout["above"] - g.cap_height(gap_font) - layout["below"] - descent
        gap_y = next_top - layout["below"] - descent - max(0, slack) / 3
        plate = g.plan_text(ground, (g.tall_left_x(left, gap_y, text, gap_font, 2), gap_y), text,
                            gap_font, g.brand.STONE, "ls", track=2)
        gaps.append((index + 1, plate))
        plates.append((f"gap{index + 1}", plate))

    g.log.info("Timeline: five entries use a date column; long proper names keep the full measure.")
    if cap < g.TALL_YEAR_RATIO * layout["label_cap"]:
        g.log.warning("Timeline: the years set under %.1f x the labels' cap height (at %.1f x, the last resort) "
                      "to keep five entries and their spans in the column.", g.TALL_YEAR_RATIO,
                      g.TALL_YEAR_RATIO_LAST)
    g.log.info("Timeline years at %d px; label and span size/air floors retained.", year_font.size)
    nodes = [entry[0] for entry in entries]
    span_centres = {index: (plate.box[1] + plate.box[3]) / 2 for index, plate in gaps}
    marks = [(index, span_centres.get(index, (nodes[index - 1] + nodes[index]) / 2)) for index in spacing.breaks]
    spine = layout["spine"]
    unit = width / g.VIDEO_WIDTH
    marks_nt = g.spine_marks((width, height), False, spine, nodes[0], nodes[-1], max(2, round(6 * unit)),
                             [(node, 16 * unit, bool(event.get("approximate"))) for node, event in zip(nodes, events)],
                             4 * unit, [position for _, position in marks], 36 * unit, 4 * unit)
    colour = g.spine_colour(ground, marks_nt[0][1])
    drift = g.GroundDrift(ground)
    schedule = g.reveal_schedule(len(events), g.CLIP_SECONDS)
    count = int(g.CLIP_SECONDS * g.VIDEO_FPS)
    last_frame = []

    def frames():
        for frame_index in range(count):
            time = frame_index / g.VIDEO_FPS
            image = drift(time)
            draw = ImageDraw.Draw(image, "RGBA")
            head = schedule.head(nodes, time, nodes[-1])
            draw.line([(spine, nodes[0]), (spine, head)], fill=(*colour, 255), width=max(2, round(6 * unit)))
            for _index, position in marks:
                if head >= position + 12 * unit:
                    g._break_glyph(draw, spine, position, False, 36 * unit, (*g.brand.STONE_LT, 255),
                                   4 * unit, ground.getpixel((spine, int(position))))
            for index, (node, originals, twins) in enumerate(entries):
                alpha = schedule.appear(index, time)
                if alpha <= 0:
                    continue
                back = schedule.focus(index, time)
                radius = 16 * unit * alpha * schedule.pulse(index, time) * (1 - 0.3 * back)
                g._node(draw, spine, node, radius, bool(events[index].get("approximate")), 1.0, 4 * unit, g.brand.INK)
                current = (alpha * (1 - g._ease((time - schedule.at[index + 1]) / 0.4))
                           if index + 1 < len(entries) else alpha)
                if current > 0.02:
                    ring = (16 + 10 * alpha) * unit
                    draw.ellipse((spine - ring, node - ring, spine + ring, node + ring),
                                 outline=(*g.GOLD, int(150 * current)), width=max(1, round(3 * unit)))
                image = g._focused(image, originals, twins, alpha, back, round(14 * unit * (1 - alpha)))
                draw = ImageDraw.Draw(image, "RGBA")
            for index, plate in gaps:
                alpha = schedule.appear(index, time)
                if alpha > 0:
                    image = g.draw_plate(image, plate, alpha, round(14 * unit * (1 - alpha)))
            if frame_index == count - 1:
                last_frame.append(image.copy())
            yield image

    result = g._encode(frames(), out, count, width=width, height=height)
    if result and last_frame:
        g.record_contrast(out, last_frame[0], plates)
        g.record_nontext(out, last_frame[0], marks_nt, drift((count - 1) / g.VIDEO_FPS))
    return result
