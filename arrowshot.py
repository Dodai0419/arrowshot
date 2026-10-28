#!/usr/bin/env python3
"""arrowshot: paint big arrows, rings and boxes with a sign on any screenshot, and pack shots into one contact sheet.

Arrow geometry is ported from franzenzenhofer/big-arrow-on-the-screen (MIT); the contact-sheet
layout from kitlangton/scrawl (MIT). Screen coordinates are top-left, y down.
"""
import argparse
import json
import math
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------- vectors and rects ----------


class P(tuple):
    def __new__(cls, x, y):
        return tuple.__new__(cls, (float(x), float(y)))

    x = property(lambda s: s[0])
    y = property(lambda s: s[1])
    __add__ = lambda a, b: P(a[0] + b[0], a[1] + b[1])
    __sub__ = lambda a, b: P(a[0] - b[0], a[1] - b[1])
    __mul__ = lambda a, k: P(a[0] * k, a[1] * k)
    length = property(lambda s: math.sqrt(s[0] * s[0] + s[1] * s[1]))
    perp = property(lambda s: P(-s[1], s[0]))  # 90 degrees counter-clockwise, y down
    dot = lambda a, b: a[0] * b[0] + a[1] * b[1]
    dist = lambda a, b: (b - a).length

    @property
    def unit(self):
        n = self.length
        return P(self[0] / n, self[1] / n) if n > 0 else P(0, 0)


class R(tuple):
    """CGRect as (x, y, w, h)."""

    def __new__(cls, x, y, w, h):
        return tuple.__new__(cls, (float(x), float(y), float(w), float(h)))

    minx = property(lambda r: r[0])
    miny = property(lambda r: r[1])
    w = property(lambda r: r[2])
    h = property(lambda r: r[3])
    maxx = property(lambda r: r[0] + r[2])
    maxy = property(lambda r: r[1] + r[3])
    center = property(lambda r: P(r[0] + r[2] / 2, r[1] + r[3] / 2))

    def inset(self, dx, dy):
        return R(self[0] + dx, self[1] + dy, self[2] - 2 * dx, self[3] - 2 * dy)

    def contains(self, o):
        return o.minx >= self.minx and o.miny >= self.miny and o.maxx <= self.maxx and o.maxy <= self.maxy

    def inter(self, o):
        x0, y0, x1, y1 = max(self.minx, o.minx), max(self.miny, o.miny), min(self.maxx, o.maxx), min(self.maxy, o.maxy)
        return R(x0, y0, x1 - x0, y1 - y0) if x1 >= x0 and y1 >= y0 else None

    def intersects(self, o):
        return min(self.maxx, o.maxx) > max(self.minx, o.minx) and min(self.maxy, o.maxy) > max(self.miny, o.miny)

    def union(self, o):
        x0, y0 = min(self.minx, o.minx), min(self.miny, o.miny)
        return R(x0, y0, max(self.maxx, o.maxx) - x0, max(self.maxy, o.maxy) - y0)

    def clamped(self, b):
        return R(min(max(self.minx, b.minx), b.maxx - self.w), min(max(self.miny, b.miny), b.maxy - self.h), self.w, self.h)


def clamp_pt(p, b):
    return P(min(max(p.x, b.minx), b.maxx), min(max(p.y, b.miny), b.maxy))


def quad(s, c, e, t):
    return s * ((1 - t) * (1 - t)) + c * (2 * (1 - t) * t) + e * (t * t)


def quad_tangent(s, c, e, t):
    return ((c - s) * (2 * (1 - t)) + (e - c) * (2 * t)).unit


def cubic(a, b, c, d, t):
    u = 1 - t
    return a * (u * u * u) + b * (3 * u * u * t) + c * (3 * u * t * t) + d * (t * t * t)


# ---------- bigarrow geometry ----------

SIZES = {"S": (10, 46, 48, 30, 130), "M": (15, 62, 64, 44, 170), "L": (21, 84, 88, 60, 220)}  # stroke, head len, head w, font, reach
_D = 1 / math.sqrt(2)
DIRS = {"top-left": P(-_D, -_D), "top-right": P(_D, -_D), "bottom-left": P(-_D, _D), "bottom-right": P(_D, _D),
        "left": P(-1, 0), "right": P(1, 0), "top": P(0, -1), "bottom": P(0, 1)}
COLORS = {"red": "FF3B1F", "orange": "FF8A00", "yellow": "FFD60A", "green": "21B24B", "teal": "00B5AD",
          "blue": "0A84FF", "purple": "8E44FF", "pink": "FF2D78", "black": "1C1C1E", "white": "FFFFFF"}
REACH_SCALES = (1.0, 0.75, 1.4, 0.55, 1.9, 2.5)
BOX_PAD, MARK_GAP, KEEP_OUT, MARGIN, OTHER_GAP = 8, 6, 120, 24, 12


def place(size, marked, usable_area, reach, forced=None, others=()):
    """Placement.place: the sign position with the most free space."""
    keep = R(marked.center.x - KEEP_OUT / 2, marked.center.y - KEEP_OUT / 2, KEEP_OUT, KEEP_OUT).union(marked.inset(-12, -12))
    usable = usable_area.inset(MARGIN, MARGIN)
    others = [o.inset(-OTHER_GAP, -OTHER_GAP) for o in others]
    names = list(DIRS)

    def rect_for(d, r):
        u = DIRS[d]
        sup = lambda w, h: abs(u.x) * w / 2 + abs(u.y) * h / 2
        c = keep.center + u * (sup(keep.w, keep.h) + r + sup(*size))
        return R(c.x - size[0] / 2, c.y - size[1] / 2, *size)

    def overlap(rect):
        return sum(s.w * s.h for s in (rect.inter(o) for o in others) if s)

    def best(tolerate, dirs):
        top = None
        for rank, d in enumerate(dirs):
            for scale in REACH_SCALES:
                rect = rect_for(d, reach * scale)
                ov = overlap(rect)
                if not usable.contains(rect) or rect.intersects(keep) or (not tolerate and ov != 0):
                    continue
                clear = min(min(rect.minx - usable.minx, usable.maxx - rect.maxx), min(rect.miny - usable.miny, usable.maxy - rect.maxy), 200)
                score = clear - abs(scale - 1) * 400 - rank * 4 - ov * 0.05
                if top is None or score > top[0]:
                    top = (score, d, rect)
        return top

    for tolerate, dirs in ((False, [forced] if forced else []), (False, names), (True, [forced] if forced else []), (True, names)):
        if dirs and (hit := best(tolerate, dirs)):
            return hit[1], hit[2]
    fallback = None
    for d in names:  # nothing fits cleanly: keep the sign on screen, overlap least
        rect = rect_for(d, reach).clamped(usable)
        s = rect.inter(keep)
        area = s.w * s.h if s else 0
        if fallback is None or area < fallback[0]:
            fallback = (area, d, rect)
    return fallback[1], fallback[2]


def junction(sign, radius, tip, stroke):
    """SignJunction.make: where the shaft leaves the sign, its outward normal and flare half width."""
    inset = radius + 2
    hor = (sign.minx + inset, max(sign.maxx - inset, sign.minx + inset))
    ver = (sign.miny + inset, max(sign.maxy - inset, sign.miny + inset))
    c, d = sign.center, tip - sign.center
    sx = math.inf if d.x == 0 else (sign.w / 2) / abs(d.x)
    sy = math.inf if d.y == 0 else (sign.h / 2) / abs(d.y)
    ex = c + d * min(sx, sy, 1)
    top = (P(0, -1), lambda a: P(a, sign.miny), hor, ex.x)
    bottom = (P(0, 1), lambda a: P(a, sign.maxy), hor, ex.x)
    left = (P(-1, 0), lambda a: P(sign.minx, a), ver, ex.y)
    right = (P(1, 0), lambda a: P(sign.maxx, a), ver, ex.y)
    crossed = min([(abs(ex.y - sign.miny), 0, top), (abs(ex.y - sign.maxy), 1, bottom),
                   (abs(ex.x - sign.minx), 2, left), (abs(ex.x - sign.maxx), 3, right)], key=lambda t: (t[0], t[1]))[2]
    edges = [crossed, top if tip.y < sign.center.y else bottom]
    normal, at, (lo, hi), exit_ = next((e for e in edges if (e[2][1] - e[2][0]) / 2 >= stroke * 0.6), edges[-1])
    half = max(min(stroke * 1.8, (hi - lo) / 2), 0)
    return at(min(max(exit_, lo + half), hi - half)), normal, half


def zigzag(tail, end, normal, stroke):
    chord = end - tail
    length = chord.length
    raw = int(math.floor(length / 70 + 0.5))
    legs = min(max(raw + 1 if raw % 2 == 0 else raw, 3), 7)
    u = chord.unit
    amp = min(stroke * 2.4, length / legs * 0.6)
    joints = [tail + u * (length * i / legs) + u.perp * (amp * (-1 if i % 2 == 0 else 1)) for i in range(1, legs)]
    if joints and (joints[0] - tail).dot(normal) < 0:  # mirror so the first leg heads away from the sign
        joints = [tail + u * (j - tail).dot(u) * 2 - (j - tail) for j in joints]
    return joints


def spiral(sign, jpt, jnormal, tip, m, bounds):
    stroke, head_len = m[0], m[1]
    c = sign.center
    gap, growth = stroke * 2.2, stroke * 2.8
    rw, rh = sign.w / 2 * math.sqrt(2) + gap, sign.h / 2 * math.sqrt(2) + gap
    off = jpt - c
    start = math.atan2(off.y / rh, off.x / rw)

    def loop(turn):
        ring = [c + P((rw + growth * i / 96) * math.cos(start + turn * 2 * math.pi * i / 96),
                      (rh + growth * i / 96) * math.sin(start + turn * 2 * math.pi * i / 96)) for i in range(97)]
        stub = jpt + jnormal * stroke
        return [quad(stub, ring[0], ring[8], i / 8) for i in range(8)] + ring[8:]

    def heading(lp):
        return (lp[-1] - lp[-2]).unit.dot((tip - lp[-1]).unit)

    a, b = loop(1), loop(-1)
    lp = b if heading(b) > heading(a) else a
    end = tip - (tip - lp[-1]).unit * (head_len * 1.2)
    exit_, tangent = lp[-1], (lp[-1] - lp[-2]).unit
    ctrl = exit_ + tangent * (exit_.dist(end) * 0.45)
    joints = [clamp_pt(p, bounds) for p in lp + [quad(exit_, ctrl, end, i / 24) for i in range(1, 25)]]
    return joints[-1], joints


def plan(target, size_wh, canvas, style="arrow", size="M", forced=None, corners="round", shape="bend", others=(), visible=None):
    """OverlayLayout.plan for one display that is the whole screenshot. target: P or R; visible: usable R."""
    m = SIZES[size]
    stroke, head_len, head_w, _, reach = m
    bounds = R(0, 0, *canvas)
    anchor = target.center if isinstance(target, R) else target
    if style == "ring":
        r = max(36, (math.hypot(target.w, target.h) / 2 if isinstance(target, R) else 0) + 12)
        mark = ("ring", anchor, r)
        area = R(anchor.x - r, anchor.y - r, 2 * r, 2 * r)
    elif isinstance(target, R):
        area = target.inset(-BOX_PAD, -BOX_PAD)
        mark = ("box", area)
    elif style == "box":
        area = R(anchor.x - 36, anchor.y - 36, 72, 72)
        mark = ("box", area)
    else:
        area, mark = R(anchor.x, anchor.y, 0, 0), ("none",)
    pad = (0.0, 0.0)
    if shape == "spiral":
        extra = stroke * 5.0 + stroke / 2 + 6
        pad = (size_wh[0] / 2 * (math.sqrt(2) - 1) + extra, size_wh[1] / 2 * (math.sqrt(2) - 1) + extra)
    direction, srect = place((size_wh[0] + 2 * pad[0], size_wh[1] + 2 * pad[1]), area, visible or bounds, reach, forced, others)
    sign = srect.inset(*pad)
    if mark[0] == "ring":
        tip = mark[1] + (sign.center - mark[1]).unit * (mark[2] + MARK_GAP)
    elif mark[0] == "box":
        b = mark[1]
        mids = [(P(b.center.x, b.miny), P(0, -1)), (P(b.center.x, b.maxy), P(0, 1)), (P(b.minx, b.center.y), P(-1, 0)), (P(b.maxx, b.center.y), P(1, 0))]
        near = mids[0]
        for cand in mids[1:]:
            if cand[0].dist(sign.center) < near[0].dist(sign.center):
                near = cand
        tip = near[0] + near[1] * MARK_GAP
    else:
        tip = anchor
    radius = min(sign.h / 2, 30) if corners == "round" else 0
    jpt, jn, jhalf = junction(sign, radius, tip, stroke)
    tail = jpt - jn * 12
    joints = []
    if shape == "spiral":
        control, joints = spiral(sign, jpt, jn, tip, m, bounds)
    elif shape == "zigzag":
        lead = jpt + jn * (stroke * 3.5)
        end = tip - (tip - lead).unit * (head_len * 1.2)
        joints = [lead] + zigzag(lead, end, jn, stroke) + [end]
        control = end
    elif shape == "straight":
        control = tail + (tip - tail) * 0.5
    else:  # bend: bow to the side furthest from the edges, leave the sign outwards first
        inside = bounds.inset(stroke, stroke)
        chord = tip - tail
        mid = tail + chord * 0.5
        off = chord.perp.unit * (chord.length * 0.22)
        clear = lambda p: min(p.x - inside.minx, inside.maxx - p.x, p.y - inside.miny, inside.maxy - p.y)
        cands = [mid + off, mid - off]
        control = cands[1] if clear(cands[1]) > clear(cands[0]) else cands[0]
        outwards, wanted = (control - tail).dot(jn), chord.length * 0.25
        if outwards < wanted:
            control = control + jn * (wanted - outwards)
        control = clamp_pt(control, inside)
    d = (tip - control).unit
    base, side = tip - d * head_len, d.perp * (head_w / 2)
    shaft_end = tip - d * (head_len * 0.7)
    head = [tip, base + side, tip - d * (head_len * 0.78), base - side]
    shaft = [tail] + joints + [shaft_end] if joints else [quad(tail, control, shaft_end, i / 48) for i in range(49)]
    leg = (tail, tail + (joints[0] - tail) * 0.5, joints[0]) if joints else (tail, control, shaft_end)
    return {"tip": tip, "tail": tail, "control": control, "direction": d, "shaft_end": shaft_end, "head": head,
            "shaft": shaft, "joints": joints, "root": root_path(jpt, jn, jhalf, leg, stroke), "sign": sign,
            "radius": radius, "mark": mark, "from": direction, "junction": (jpt, jn, jhalf), "leg": leg, "stroke": stroke}


def root_path(jpt, normal, half, leg, stroke, steps=16):
    """ArrowRoot: the flared join where the shaft grows out of the sign, as a polygon."""
    s, c, e = leg
    scale = min(1, s.dist(e) / (stroke * 9))
    flare = stroke * 2.6 * scale
    lo, hi = 0.0, 1.0
    for _ in range(24):
        mid = (lo + hi) / 2
        if quad(s, c, e, mid).dist(jpt) < flare:
            lo = mid
        else:
            hi = mid
    t = (lo + hi) / 2
    neck, tan = quad(s, c, e, t), quad_tangent(s, c, e, t)
    along, sidev = normal.perp, tan.perp
    orient = 1 if along.dot(sidev) >= 0 else -1
    base = max(min(half * scale, half), stroke / 2)
    edge = jpt - normal * 4
    flanks = []
    for sg in (orient, -orient):
        on_shaft = neck + sidev * (stroke / 2 * sg * orient)
        flanks.append((edge + along * (base * sg), edge + along * (base * 0.35 * sg), on_shaft - tan * (flare * 0.55), on_shaft))
    f0, f1 = flanks
    pts = [f0[0] - normal * 10] + [cubic(*f0, i / steps) for i in range(steps + 1)]
    pts += [cubic(f1[3], f1[2], f1[1], f1[0], i / steps) for i in range(steps + 1)] + [f1[0] - normal * 10]
    return pts


# ---------- drawing ----------

SS = 4  # supersampling factor for anti-aliased edges


def parse_color(raw):
    key = raw.lower().strip()
    hexs = COLORS.get(key, key.lstrip("#"))
    if len(hexs) == 3:
        hexs = "".join(ch * 2 for ch in hexs)
    try:
        v = int(hexs, 16)
        if len(hexs) != 6:
            raise ValueError
    except ValueError:
        raise SystemExit(f"colour '{raw}' is not a preset ({', '.join(COLORS)}) or a hex like #FF3B1F")
    return (v >> 16 & 255, v >> 8 & 255, v & 255)


def contrast(rgb):
    lum = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255
    return (28, 28, 31) if lum > 0.6 else (255, 255, 255)


def font(px):
    for name in ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            pass
    return ImageFont.load_default(px)


def layout_text(text, size, canvas_w):
    """Wrap to at most 3 lines in 60% of the width, shrinking the font down to 24 px if needed."""
    max_w = canvas_w * 0.6 - 60
    px = SIZES[size][3]
    while True:
        f = font(px)
        lines, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if cur and f.getlength(trial) > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        lines.append(cur)
        if len(lines) <= 3 or px <= 24:
            break
        px -= 2
    lh = round(px * 1.2)
    w = max(f.getlength(ln) for ln in lines)
    return f, lines, lh, (math.ceil(30 + w + 30), math.ceil(lh * len(lines) + 32))


def stroke_poly(d, pts, width, fill):
    pts = [(x * SS, y * SS) for x, y in pts]
    d.line(pts, fill=fill, width=round(width * SS), joint="curve")
    r = width * SS / 2
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def fill_poly(d, pts, fill, outline=None, width=0):
    pts = [(x * SS, y * SS) for x, y in pts]
    d.polygon(pts, fill=fill)
    if outline:
        d.line(pts + [pts[0]], fill=outline, width=round(width * SS), joint="curve")


def draw_marks(shot, marks):
    """Draw every mark (dict: at|rect, text, from, style, shape, color, size, corners) on a copy of shot."""
    base = shot.convert("RGBA")
    W, H = base.size
    planned, others = [], []
    for mk in marks:
        size = mk.get("size", "M").upper()
        if size not in SIZES:
            raise SystemExit(f"size '{mk.get('size')}' is not one of S, M, L")
        f, lines, lh, sign_wh = layout_text(mk.get("text", ""), size, W)
        if "rect" in mk:
            x, y, w, h = mk["rect"]
            if w <= 0 or h <= 0:
                raise SystemExit("rect needs a positive width and height")
            target = R(x, y, w, h)
        else:
            target = P(*mk["at"])
        forced = mk.get("from", "auto")
        if forced != "auto" and forced not in DIRS:
            raise SystemExit(f"from '{forced}' is not one of auto, {', '.join(DIRS)}")
        g = plan(target, sign_wh, (W, H), mk.get("style", "arrow"), size, None if forced == "auto" else forced,
                 mk.get("corners", "round"), mk.get("shape", "bend"), others)
        others.append(g["sign"])
        planned.append((g, parse_color(mk.get("color", "red")), size, f, lines, lh))

    layer = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    sil = Image.new("L", (W * SS, H * SS), 0)
    ds = ImageDraw.Draw(sil)

    def parts(g, d, color, extra):
        stroke, s = g["stroke"], g["sign"]
        d.rounded_rectangle([(s.minx - extra / 2) * SS, (s.miny - extra / 2) * SS, (s.maxx + extra / 2) * SS, (s.maxy + extra / 2) * SS],
                            radius=(g["radius"] + extra / 2) * SS, fill=color)
        m = g["mark"]
        if m[0] == "ring":
            c, r, w = m[1], m[2], (6 + extra) * SS
            d.ellipse([(c.x - r) * SS - w / 2, (c.y - r) * SS - w / 2, (c.x + r) * SS + w / 2, (c.y + r) * SS + w / 2], outline=color, width=round(w))
        elif m[0] == "box":
            b, w = m[1], (6 + extra) * SS
            d.rounded_rectangle([b.minx * SS - w / 2, b.miny * SS - w / 2, b.maxx * SS + w / 2, b.maxy * SS + w / 2], radius=10 * SS, outline=color, width=round(w))
        fill_poly(d, g["root"], color, color if extra else None, extra)
        stroke_poly(d, g["shaft"], stroke + extra, color)
        fill_poly(d, g["head"], color, color if extra else None, extra)

    for g, color, *_ in planned:
        parts(g, ds, 255, 6)
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))  # blurred at 1x: a 4x blur costs seconds
    shadow.putalpha(sil.reduce(SS).point(lambda v: v * 0.35).filter(ImageFilter.GaussianBlur(10)))
    shadow = shadow.transform((W, H), Image.AFFINE, (1, 0, 0, 0, 1, -3))
    for g, color, *_ in planned:  # every border below every fill
        parts(g, d, contrast(color), 6)
    for g, color, *_ in planned:
        parts(g, d, color, 0)
    result = Image.alpha_composite(Image.alpha_composite(base, shadow), layer.reduce(SS))
    td = ImageDraw.Draw(result)
    for g, color, size, f, lines, lh in planned:  # text above the arrow
        s = g["sign"]
        y = s.miny + (s.h - lh * len(lines)) / 2
        for ln in lines:
            td.text((s.minx + 30, y + lh / 2), ln, font=f, fill=contrast(color), anchor="lm")
            y += lh
    return result.convert(shot.mode if shot.mode in ("RGB", "RGBA") else "RGB"), [p[0] for p in planned]


# ---------- scrawl contact sheet ----------

SHEET_PAD, SHEET_GAP, SHEET_BG, MAX_EDGE, MAX_PIXELS = 6, 6, (28, 29, 34), 1600.0, 1_560_000.0


def plan_rows(aspects):
    """plan_contact_sheet_rows: justified rows of 1..6 frames, balanced areas, sheet aspect near 1.05-1.25."""
    n = len(aspects)
    if n <= 1:
        return [(0, n)] if n else []
    if n == 2:
        return [(0, 1), (1, 2)] if f32(aspects[0] + aspects[1]) > f32(3.6) else [(0, 2)]
    avg = sum(aspects) / n
    target_sheet = 1.05 if avg < 0.7 else 1.25
    target_row = max(math.sqrt(sum(aspects) * target_sheet), avg)
    best = [math.inf, None]

    def cost(rows):
        row_a = [sum(aspects[a:b]) for a, b in rows]
        rc = len(rows)
        sheet = 1 / sum(1 / a for a in row_a)
        c = math.log(sheet / target_sheet) ** 2 * 1.8
        areas = [aspects[i] / (ra * ra) for (a, b), ra in zip(rows, row_a) for i in range(a, b)]
        logs = [math.log(x) for x in areas]
        mean = sum(logs) / n
        c += sum((x - mean) ** 2 for x in logs) / n * 8
        srt = sorted(areas)
        med = max(srt[(n - 1) // 2] + srt[n // 2], 1e-6) * 0.5
        for ar in areas:
            ratio = ar / med
            if ratio < 0.58:
                c += ((0.58 - ratio) / 0.58) ** 2 * 18
            if ratio > 2.3:
                c += ((ratio - 2.3) / 2.3) ** 2 * 4
        mean_log = sum(math.log(a) for a in row_a) / rc
        cramp = (rc - 4) * 2.5 if rc > 4 else 0.0
        var = pen = tie = 0.0
        for r, ((a, b), ra) in enumerate(zip(rows, row_a)):
            var += (math.log(ra) - mean_log) ** 2
            pen += math.log(ra / target_row) ** 2
            for ai in aspects[a:b]:
                wf = ai / ra * min(sheet, 1)
                if wf < 0.16:
                    cramp += ((0.16 - wf) / 0.16) ** 2 * 8
            cols = b - a
            if cols > 4 and avg > 0.75:
                cramp += (cols - 4) * 2
            if cols > 3 and avg > 1.15:
                cramp += (cols - 3) * 2
            tie += r * 1e-4 * cols
        return c + var / rc * 0.6 + pen / rc * 0.4 + cramp + tie

    def search(i, rows):
        if i == n:
            c = cost(rows)
            if c < best[0]:
                best[:] = [c, list(rows)]
            return
        for k in range(1, min(6, n - i) + 1):
            if k == 1 and aspects[i] < 1.85:
                continue
            search(i + k, rows + [(i, i + k)])

    search(0, [])
    return best[1]


def f32(v):
    return struct.unpack("f", struct.pack("f", v))[0]


def f32sum(values):
    total = 0.0
    for v in values:
        total = f32(total + v)
    return total


def sheet_cells(sizes):
    """Cell rects (x, y, w, h) for frames of the given pixel sizes, and the sheet size. f32 like scrawl."""
    aspects = [min(max(f32(max(w, 1) / max(h, 1)), 0.25), 4.5) for w, h in sizes]
    rows = plan_rows(aspects)
    row_a = [max(f32sum(aspects[a:b]), 0.1) for a, b in rows]
    sheet_aspect = f32(1 / max(f32sum(f32(1 / a) for a in row_a), 0.01))
    pad, gap = float(SHEET_PAD), float(SHEET_GAP)
    native = max(sum(sizes[i][0] for i in range(a, b)) + gap * (b - a - 1) for a, b in rows)
    desired = max(native, 640.0 if len(sizes) == 1 else 960.0)
    w_inner = min(MAX_EDGE - 2 * pad, desired) if sheet_aspect >= 1 else min(f32((MAX_EDGE - 2 * pad) * sheet_aspect), desired)
    h_inner = f32(f32sum(f32(max(w_inner - gap * (b - a - 1), 32.0) / ra) for (a, b), ra in zip(rows, row_a)) + gap * (len(rows) - 1))
    total_w, total_h = f32(w_inner + 2 * pad), f32(h_inner + 2 * pad)
    scale = 1.0
    if max(total_w, total_h) > MAX_EDGE:
        scale = min(scale, f32(MAX_EDGE / max(total_w, total_h)))
    if f32(total_w * total_h) > MAX_PIXELS:
        scale = min(scale, f32(math.sqrt(f32(MAX_PIXELS / f32(total_w * total_h)))))
    if scale < 1:
        total_w = f32(max(f32(f32(total_w * scale) - 2 * pad), 64.0) + 2 * pad)
    inner = int(max(round_half_away(total_w), 64.0)) - 2 * SHEET_PAD
    heights = [int(max(round_half_away(f32(max(inner - SHEET_GAP * (b - a - 1), 1) / ra)), 1.0)) for (a, b), ra in zip(rows, row_a)]
    cells, y = [None] * len(sizes), SHEET_PAD
    for (a, b), ra, rh in zip(rows, row_a, heights):
        avail, cum = max(inner - SHEET_GAP * (b - a - 1), 0), 0.0
        for j, i in enumerate(range(a, b)):
            x0 = int(round_half_away(f32(avail * f32(cum / ra))))
            cum = f32(cum + aspects[i])
            x1 = avail if i == b - 1 else int(round_half_away(f32(avail * f32(cum / ra))))
            cells[i] = (SHEET_PAD + x0 + j * SHEET_GAP, y, max(x1 - x0, 1), rh)
        y += rh + SHEET_GAP
    return cells, (inner + 2 * SHEET_PAD, 2 * SHEET_PAD + sum(heights) + SHEET_GAP * (len(rows) - 1))


def round_half_away(v):
    return math.floor(v + 0.5) if v >= 0 else math.ceil(v - 0.5)


def contact_sheet(images):
    images = images[:12]  # scrawl's MAX_KEYFRAMES
    cells, size = sheet_cells([im.size for im in images])
    sheet = Image.new("RGB", size, SHEET_BG)
    d = ImageDraw.Draw(sheet)
    f = font(15)
    for n, (im, (x, y, w, h)) in enumerate(zip(images, cells), 1):
        sheet.paste(im.convert("RGB").resize((w, h), Image.LANCZOS), (x, y))
        d.ellipse((x + 6, y + 6, x + 30, y + 30), fill=(255, 59, 31), outline=(255, 255, 255), width=2)
        d.text((x + 18, y + 18), str(n), font=f, fill=(255, 255, 255), anchor="mm")
    return sheet


# ---------- demo screenshot (drawn here, no third-party art) ----------


def demo_screen(w=1280, h=800):
    im = Image.new("RGB", (w, h), (233, 236, 241))
    d = ImageDraw.Draw(im)
    for i in range(h):  # soft wallpaper gradient
        d.line([(0, i), (w, i)], fill=(int(70 + 60 * i / h), int(110 + 50 * i / h), int(170 + 40 * i / h)))
    d.rectangle((0, 0, w, 28), fill=(245, 245, 247))
    d.text((16, 14), "Finder   File   Edit   View   Window   Help", font=font(14), fill=(30, 30, 30), anchor="lm")
    d.rounded_rectangle((300, 150, 980, 640), 14, fill=(250, 250, 252), outline=(200, 200, 205))
    d.rounded_rectangle((300, 150, 980, 196), 14, fill=(236, 236, 240))
    d.rectangle((300, 180, 980, 196), fill=(236, 236, 240))
    for k, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse((318 + 22 * k, 166, 332 + 22 * k, 180), fill=c)
    d.text((640, 173), "Privacy & Security", font=font(16), fill=(40, 40, 45), anchor="mm")
    rows = ["Terminal", "Visual Studio Code", "Docker Desktop", "Zoom"]
    for k, name in enumerate(rows):
        y = 240 + 62 * k
        d.rounded_rectangle((340, y, 940, y + 50), 8, fill=(255, 255, 255), outline=(225, 225, 230))
        d.rounded_rectangle((356, y + 11, 384, y + 39), 6, fill=[(40, 40, 40), (0, 122, 204), (36, 150, 237), (45, 140, 255)][k])
        d.text((400, y + 25), name, font=font(17), fill=(30, 30, 35), anchor="lm")
        on = k != 0
        d.rounded_rectangle((866, y + 13, 914, y + 37), 12, fill=(52, 199, 89) if on else (205, 205, 210))
        cx = 902 if on else 878
        d.ellipse((cx - 10, y + 15, cx + 10, y + 35), fill=(255, 255, 255))
    d.rounded_rectangle((340, 500, 372, 528), 6, outline=(170, 170, 175), width=2)
    d.text((356, 514), "+", font=font(20), fill=(90, 90, 95), anchor="mm")
    d.rounded_rectangle((700, 580, 810, 616), 8, fill=(255, 255, 255), outline=(190, 190, 195))
    d.text((755, 598), "Cancel", font=font(16), fill=(30, 30, 35), anchor="mm")
    d.rounded_rectangle((826, 580, 940, 616), 8, fill=(0, 122, 255))
    d.text((883, 598), "Allow", font=font(16), fill=(255, 255, 255), anchor="mm")
    return im


DEMO_MARKS = {
    "allow": [{"rect": [826, 580, 114, 36], "text": "Click Allow to finish setup", "color": "red"}],
    "toggle": [{"rect": [866, 253, 48, 24], "text": "Switch Terminal on", "style": "ring", "color": "green", "shape": "zigzag"}],
    "steps": [{"rect": [866, 253, 48, 24], "text": "1. Turn this on", "style": "ring", "color": "purple", "size": "S", "from": "top-left"},
              {"rect": [340, 500, 32, 28], "text": "2. Not listed? Plus", "style": "box", "color": "orange", "size": "S", "from": "bottom-left"},
              {"rect": [826, 580, 114, 36], "text": "3. Allow", "color": "teal", "size": "S", "from": "bottom-left"}],
    "spiral": [{"at": [755, 598], "text": "Not this one", "color": "black", "shape": "spiral", "size": "S"}],
}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="arrowshot", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("point", help="draw one mark (flags) or many (--marks file.json) on a screenshot")
    p.add_argument("shot")
    p.add_argument("-o", "--out")
    p.add_argument("--at", help="x,y point to hit")
    p.add_argument("--rect", help="x,y,w,h element to box")
    p.add_argument("--text", default="")
    p.add_argument("--from", dest="frm", default="auto", help="auto or " + ", ".join(DIRS))
    p.add_argument("--style", default="arrow", choices=["arrow", "ring", "box"])
    p.add_argument("--shape", default="bend", choices=["bend", "straight", "zigzag", "spiral"])
    p.add_argument("--color", default="red")
    p.add_argument("--size", default="M")
    p.add_argument("--corners", default="round", choices=["round", "sharp"])
    p.add_argument("--marks", help="JSON list of marks with the same keys")
    p.add_argument("--json", action="store_true", help="print the planned geometry")
    s = sub.add_parser("sheet", help="pack screenshots into one numbered contact sheet")
    s.add_argument("shots", nargs="+")
    s.add_argument("-o", "--out", required=True)
    m = sub.add_parser("demo", help="write demo images")
    m.add_argument("--out", default="demo")
    a = ap.parse_args(argv)

    if a.cmd == "point":
        if a.marks:
            marks = json.loads(Path(a.marks).read_text(encoding="utf-8"))
        else:
            if bool(a.at) == bool(a.rect):
                ap.error("give exactly one of --at x,y or --rect x,y,w,h")
            nums = [float(v) for v in (a.at or a.rect).split(",")]
            if len(nums) != (2 if a.at else 4) or not all(map(math.isfinite, nums)):
                ap.error("--at needs 2 numbers, --rect needs 4")
            marks = [{"at" if a.at else "rect": nums, "text": a.text, "from": a.frm, "style": a.style,
                      "shape": a.shape, "color": a.color, "size": a.size, "corners": a.corners}]
        img, plans = draw_marks(Image.open(a.shot), marks)
        out = a.out or str(Path(a.shot).with_name(Path(a.shot).stem + "-marked.png"))
        img.save(out)
        if a.json:
            print(json.dumps([{"tip": g["tip"], "sign": g["sign"], "from": g["from"]} for g in plans]))
        else:
            for g in plans:
                print(f"{out}: arrow from {g['from']} to ({g['tip'].x:.0f},{g['tip'].y:.0f}), sign at ({g['sign'].minx:.0f},{g['sign'].miny:.0f})")
    elif a.cmd == "sheet":
        contact_sheet([Image.open(f) for f in a.shots]).save(a.out)
        print(f"{a.out}: {min(len(a.shots), 12)} shots")
    else:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        screen = demo_screen()
        screen.save(out / "screen.png")
        shots = []
        for name, marks in DEMO_MARKS.items():
            img, _ = draw_marks(screen, marks)
            img.save(out / f"{name}.png")
            shots.append(img)
            print(f"{out / (name + '.png')}: {len(marks)} mark(s)")
        contact_sheet(shots).save(out / "sheet.png")
        print(f"{out / 'sheet.png'}: {len(shots)} shots")


if __name__ == "__main__":
    sys.exit(main())
