"""A faked vision model for the callout tests (round 5 section 9 trust flow): it answers like a model that sees the
features `feats` ((label, x, y[, w, h[, kind]]) as fractions of the whole photo) wherever it is asked, on every view
a location pass is shown (callouts.locate_view), in the checker's blind location, its blind look at a crosshair, the
text match and the decoy. Overrides make one step say something else."""
import base64
import io
import re

from PIL import Image

from src import callouts, free_llm, llm


def _box(f):
    """The feature's box as a model gives it: inside the photo (a box running off its edge is cut at the edge)."""
    return [max(0.0, f[1] - f[3] / 2), max(0.0, f[2] - f[4] / 2), min(1.0, f[1] + f[3] / 2), min(1.0, f[2] + f[4] / 2)]


def install(monkeypatch, feats, *, ready=True, image=1, confidence="high", same_site=True, look=None, decoy=None,
            box=None, fail=None, passes=None, calls=None, sizes=None, served=None):
    """`look`: {label: what the blind look names at that label's point}; `decoy`: {label: the decoy's yes/no};
    `box`: {label: the blind location's box, or None for no box}; `fail`: {label: an exception every check about it
    raises}; `passes`: {pass number (1-3): {label: (x, y) it answers there instead}}; `calls`: the schemas asked,
    in order; `sizes`: the image sizes the location passes were sent; `served`: {step ("locate", "check", "match"):
    the model that answers it, or a list of them, one per call in turn, None for an unknown one}, by default the
    configured model for the step (callouts.models())."""
    feats = [(f[0], f[1], f[2], *(f[3:5] if len(f) > 4 else (0.06, 0.06)), f[5] if len(f) > 5 else "point")
             for f in feats]
    by_label = {f[0].lower(): f for f in feats}
    look, decoy, box, fail, passes = look or {}, decoy or {}, box or {}, fail or {}, passes or {}
    served = dict(served or {})
    asked = {"locate": 0, "check": 0, "match": 0}

    def serve(step):
        """Notes the model that answered this call, as free_llm and llm do."""
        who = served.get(step, callouts.models()[step])
        if isinstance(who, list):
            who = who[min(asked[step], len(who) - 1)]
        asked[step] += 1
        llm.note_served(who)
    seen_passes = []
    monkeypatch.setattr(free_llm, "ready", lambda: ready)

    def text_of(content):
        if isinstance(content, str):
            return content
        return "\n".join(c.get("text", "") for c in content if c.get("type") == "text")

    def field(text, name):
        m = re.search(rf"^{name}: (.*)$", text, re.MULTILINE)
        return m.group(1).strip() if m else None

    def point(text):
        m = re.search(r"Point: x=([0-9.]+), y=([0-9.]+)", text)
        return (float(m.group(1)), float(m.group(2))) if m else None

    def at(xy):
        inside = [f for f in feats if _box(f)[0] <= xy[0] <= _box(f)[2] and _box(f)[1] <= xy[1] <= _box(f)[3]]
        return min(inside, key=lambda f: (f[1] - xy[0]) ** 2 + (f[2] - xy[1]) ** 2) if inside else None

    def call(system, content, schema, **kw):
        if calls is not None:
            calls.append(schema.__name__)
        text = text_of(content)
        serve("locate" if schema is callouts.CalloutPlan else "match" if schema is callouts.LabelMatch else "check")
        if schema is callouts.CalloutPlan:
            view = callouts.locate_view.get()
            seen_passes.append(view)
            n = len(seen_passes)
            if sizes is not None:
                sizes.extend(Image.open(io.BytesIO(base64.b64decode(c["source"]["data"]))).size
                             for c in content if c.get("type") == "image")
            out = []
            for f in feats:
                x, y = passes.get(n, {}).get(f[0], (f[1], f[2]))
                loc = callouts.Located(label=f[0], visible=True, x=x, y=y, w=f[3], h=f[4], confidence=confidence,
                                       kind=f[5], box=_box((f[0], x, y, f[3], f[4])))
                out.append(callouts.to_view(loc, view))
            return callouts.CalloutPlan(image=image, features=out)
        label = field(text, "Feature") or field(text, "Label")
        err = fail.get(label)
        if err is not None:
            raise err
        if schema is callouts.CheckLocate:
            f = by_label.get((label or "").lower())
            b = box.get(label, _box(f) if f else [])
            return callouts.CheckLocate(visible=f is not None, confidence=confidence, same_site=same_site,
                                        box=b or [])
        if schema is callouts.CheckLook:
            f = at(point(text))
            name = look.get(f[0], f[0]) if f else "plain rock"
            if isinstance(name, Exception):
                raise name
            return callouts.CheckLook(feature=name, confidence=confidence)
        if schema is callouts.LabelMatch:
            same = (field(text, "Described") or "").lower() == (label or "").lower()
            return callouts.LabelMatch(same_feature=same, confidence=confidence)
        if schema is callouts.LabelCheck:   # the decoy
            f = at(point(text))
            yes = decoy.get(label, f is not None and f[0].lower() == (label or "").lower())
            return callouts.LabelCheck(shows_feature=yes, confidence=confidence, note="")
        raise AssertionError(schema)
    monkeypatch.setattr(free_llm, "call", call)
    return calls
