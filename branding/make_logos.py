"""Generate 3EDIČ logo proposals as real vector paths (cut from Bitcount Prop Single at weight 370), so they do not
depend on the font being installed. Output: branding/options/*.svg and a contact sheet HTML.

usage: python -I make_logos.py OUT_DIR
"""
import sys
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

FONTS = Path(__file__).resolve().parent.parent / "edic" / "ui" / "fonts"
INK, PAPER, SIGNAL, MUTED_D, MUTED_L = "#EDECE6", "#0B0B0C", "#E80700", "#34343A", "#9C9B95"
BG_D, BG_L = "#060607", "#EDECE6"


def load(name, wght=370):
    f = TTFont(FONTS / name)
    return instancer.instantiateVariableFont(f, {"wght": wght}, inplace=False)


LATIN, EXT = load("bitcount-prop-single-latin.woff2"), load("bitcount-prop-single-latin-ext.woff2")


def glyph(ch):
    font = LATIN if ord(ch) < 0x100 else EXT
    name = font.getBestCmap()[ord(ch)]
    return font, name


def contours(ch):
    """list of contours as recorded pen values (font units, y up) + advance"""
    font, name = glyph(ch)
    gs = font.getGlyphSet()
    rec = DecomposingRecordingPen(gs)
    gs[name].draw(rec)
    out, cur = [], []
    for op, args in rec.value:
        cur.append((op, args))
        if op in ("closePath", "endPath"):
            out.append(cur)
            cur = []
    return out, gs[name].width


def path_d(cs, x, y, scale, flip=True):
    """SVG path for contours placed at (x, y baseline) with scale; y axis flipped to SVG space."""
    pen = SVGPathPen(None)
    tp = TransformPen(pen, (scale, 0, 0, -scale if flip else scale, x, y))
    for c in cs:
        for op, args in c:
            getattr(tp, op)(*args)
    return pen.getCommands()


def bounds(cs):
    bp = BoundsPen(None)
    for c in cs:
        for op, args in c:
            getattr(bp, op)(*args)
    return bp.bounds


CAP = 700  # cap height in font units, measured below


def word(text, size, x=0, y=None, tracking=30, caron_red=False):
    """-> (path_ink, path_red, width, ascent_top, baseline) in SVG units. Red = háček contours when caron_red."""
    scale = size / 1000.0
    y = y if y is not None else size
    ink, red, cx = [], [], x
    for ch in text:
        cs, adv = contours(ch)
        if caron_red and ch == "Č":
            base_top = CAP + 5
            red_c = [c for c in cs if (bounds([c]) or (0, 0, 0, 0))[1] > base_top]
            ink_c = [c for c in cs if c not in red_c]
            ink.append(path_d(ink_c, cx, y, scale)); red.append(path_d(red_c, cx, y, scale))
        else:
            ink.append(path_d(cs, cx, y, scale))
        cx += (adv + tracking) * scale
    return " ".join(ink), " ".join(red), cx - x - tracking * scale, y


def cap_height():
    cs, _ = contours("E")
    return bounds(cs)[3]


CAP = cap_height()


def svg(w, h, body, bg=None):
    rect = f'<rect width="{w}" height="{h}" fill="{bg}"/>' if bg else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">{rect}{body}</svg>'


def cut_poly(x, y, w, h, c):
    """rectangle with top-right and bottom-left corners cut at 45 degrees (the design system's shape)"""
    return f"{x},{y} {x + w - c},{y} {x + w},{y + c} {x + w},{y + h} {x + c},{y + h} {x},{y + h - c}"


def layout(text, size, tracking=30, caron_red=False):
    """Place glyphs on a baseline at origin (0,0), SVG y-down. -> (list of (ink_path, red_path), (xmin, ymin, xmax, ymax))."""
    sc = size / 1000.0
    x, parts, bb = 0.0, [], [1e9, 1e9, -1e9, -1e9]
    for ch in text:
        cs, adv = contours(ch)
        red_c = []
        if caron_red and ch == "\u010c":
            red_c = [c for c in cs if (bounds([c]) or (0, 0, 0, 0))[1] > CAP + 5]
            cs = [c for c in cs if c not in red_c]
        for c in cs + red_c:
            b = bounds([c])
            if b:
                bb = [min(bb[0], x + b[0] * sc), min(bb[1], -b[3] * sc), max(bb[2], x + b[2] * sc), max(bb[3], -b[1] * sc)]
        parts.append((path_d(cs, x, 0, sc), path_d(red_c, x, 0, sc) if red_c else ""))
        x += (adv + tracking) * sc
    return parts, tuple(bb)


def place(text, size, cx, cy, **kw):
    """Word centred (by its ink) on (cx, cy). -> (ink path d, red path d, bbox in final coordinates)."""
    parts, (x0, y0, x1, y1) = layout(text, size, **kw)
    dx, dy = cx - (x0 + x1) / 2, cy - (y0 + y1) / 2
    t = f'translate({dx:.1f},{dy:.1f})'
    ink = " ".join(a for a, _ in parts)
    red = " ".join(b for _, b in parts if b)
    return t, ink, red, (x0 + dx, y0 + dy, x1 + dx, y1 + dy)


def variants(dark):
    ink = INK if dark else PAPER
    orbit = MUTED_D if dark else MUTED_L
    bg = BG_D if dark else BG_L
    glow = ('<filter id="g" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" result="b"/>'
            '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>') if dark else ""
    gf = ' filter="url(#g)"' if dark else ""
    out = {}
    S, pad = 120, 50

    def lockup(**kw):
        parts, bb = layout("3EDI\u010c", S, **kw)
        a, r = " ".join(x for x, _ in parts), " ".join(y for _, y in parts if y)
        return None, a, r, bb, bb[2] - bb[0] + 2 * pad, bb[3] - bb[1] + 2 * pad

    # 01 plain wordmark (what the app uses now)
    _, a, _, bb, W, H = lockup()
    out["01-wordmark"] = svg(round(W), round(H), f'<path d="{a}" transform="translate({pad - bb[0]:.1f},{pad - bb[1]:.1f})" fill="{ink}"/>')

    # 02 red háček
    _, a, r, bb, W, H = lockup(caron_red=True)
    tr = f'translate({pad - bb[0]:.1f},{pad - bb[1]:.1f})'
    out["02-red-caron"] = svg(round(W), round(H), f'<defs>{glow}</defs><path d="{a}" transform="{tr}" fill="{ink}"/><path d="{r}" transform="{tr}" fill="{SIGNAL}"{gf}/>')

    # 03 cut-corner frame (the system's shape) around the wordmark, one red dot in the cut corner
    parts, (x0, y0, x1, y1) = layout("3EDI\u010c", 92)
    fw, fh, m = (x1 - x0) + 120, (y1 - y0) + 100, 14
    d = " ".join(a_ for a_, _ in parts)
    out["03-frame"] = svg(round(fw + 2 * m), round(fh + 2 * m),
                          f'<defs>{glow}</defs><polygon points="{cut_poly(m, m, fw, fh, 40)}" fill="none" stroke="{ink}" stroke-width="3"/>'
                          f'<path d="{d}" transform="translate({m + 60 - x0:.1f},{m + 50 - y0:.1f})" fill="{ink}"/>'
                          f'<circle cx="{m + fw - 26}" cy="{m + 26}" r="7" fill="{SIGNAL}"{gf}/>')

    # 04 solid red plate (like the primary key): white wordmark on the one red surface
    parts, (x0, y0, x1, y1) = layout("3EDI\u010c", 92)
    fw, fh, m = (x1 - x0) + 110, (y1 - y0) + 90, 6
    d = " ".join(a_ for a_, _ in parts)
    out["04-red-plate"] = svg(round(fw + 2 * m), round(fh + 2 * m),
                              f'<polygon points="{cut_poly(m, m, fw, fh, 40)}" fill="{SIGNAL}"/>'
                              f'<path d="{d}" transform="translate({m + 55 - x0:.1f},{m + 45 - y0:.1f})" fill="#FFFFFF"/>')

    # 05 sorted dots: a dotted line under the wordmark, one group of dots per letter, the last dot red
    parts, bbx = layout("3EDI\u010c", S)
    xs = []
    x = 0.0
    sc = S / 1000
    for ch in "3EDI\u010c":
        _, adv = contours(ch)
        xs.append((x, x + adv * sc))
        x += (adv + 30) * sc
    W = bbx[2] - bbx[0] + 2 * pad
    dots, y_dots = [], bbx[3] + 52
    for i, (a_, b_) in enumerate(xs):
        n = max(2, round((b_ - a_) / 17))
        for k in range(n):
            cx_ = a_ + (k + 0.5) * (b_ - a_) / n
            last = i == len(xs) - 1 and k == n - 1
            dots.append(f'<circle cx="{cx_ - bbx[0] + pad:.1f}" cy="{y_dots:.1f}" r="{5.2 if not last else 7}" fill="{SIGNAL if last else ink}" opacity="{1 if last else 0.75}"{gf if last else ""}/>')
    d = " ".join(a_ for a_, _ in parts)
    out["05-sorted-dots"] = svg(round(W), round(bbx[3] - bbx[1] + 2 * pad + 40),
                                f'<defs>{glow}</defs><path d="{d}" transform="translate({pad - bbx[0]:.1f},{pad - bbx[1]:.1f})" fill="{ink}"/>'
                                f'<g transform="translate(0,{pad - bbx[1] - bbx[3] + bbx[3]:.1f})">' + "".join(dots) + "</g>")

    # ---- app icons ----
    I = 512

    def icon_bg(shape="round"):
        if shape == "cut":
            return f'<polygon points="{cut_poly(0, 0, I, I, 150)}" fill="{bg}"/>'
        return f'<rect width="{I}" height="{I}" rx="112" fill="{bg}"/>'

    # 06 monogram 3E, red dot top right
    t, a, _, bb = place("3E", 360, I / 2, I / 2 + 8)
    out["06-icon-3E"] = svg(I, I, f'<defs>{glow}</defs>{icon_bg()}<path d="{a}" transform="{t}" fill="{ink}"/><circle cx="{I - 84}" cy="84" r="20" fill="{SIGNAL}"{gf}/>')

    # 07 red cut-corner tile with a white 3
    t, a, _, bb = place("3", 400, I / 2, I / 2)
    out["07-icon-red"] = svg(I, I, f'<polygon points="{cut_poly(0, 0, I, I, 150)}" fill="{SIGNAL}"/><path d="{a}" transform="{t}" fill="#FFFFFF"/>')

    # 08 sorted grid: 3 columns of dots, one photo lands red
    pts = []
    for r_ in range(3):
        for c_ in range(3):
            cx_, cy_ = 160 + c_ * 96, 160 + r_ * 96
            red = (r_, c_) == (1, 2)
            pts.append(f'<circle cx="{cx_}" cy="{cy_}" r="{27 if red else 22}" fill="{SIGNAL if red else ink}"{gf if red else ""}/>')
    out["08-icon-grid"] = svg(I, I, f'<defs>{glow}</defs>{icon_bg()}' + "".join(pts))
    return out


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tiles = []
    for dark in (True, False):
        for name, s in variants(dark).items():
            (out / f"{name}-{'dark' if dark else 'light'}.svg").write_text(s)
    names = sorted({p.name.rsplit("-", 1)[0] for p in out.glob("*-dark.svg")})
    labels = {"01-wordmark": "01 · Wordmark", "02-red-caron": "02 · Red háček", "03-frame": "03 · Cut-corner frame", "04-red-plate": "04 · Red plate",
              "05-sorted-dots": "05 · Sorted dots", "06-icon-3E": "06 · Icon 3E", "07-icon-red": "07 · Icon red tile", "08-icon-grid": "08 · Icon grid"}
    cells = []
    for n in names:
        d = (out / f"{n}-dark.svg").read_text()
        l = (out / f"{n}-light.svg").read_text()
        icon = "icon" in n
        cells.append(f'<section class="{"icon" if icon else "word"}"><h3>{labels.get(n, n)}</h3><div class="pair"><div class="d">{d}</div><div class="l">{l}</div></div></section>')
    html = f"""<!doctype html><meta charset=utf-8><title>3EDIČ logo options</title>
<style>body{{margin:0;background:#060607;color:#8A8A86;font:12px 'Martian Mono',monospace;padding:32px}}h1{{font:400 28px 'Martian Mono';color:#EDECE6;margin:0 0 24px}}
.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:28px}}section{{border-top:1px solid #1F1F23;padding-top:10px}}h3{{margin:0 0 12px;font-weight:400;letter-spacing:.16em;text-transform:uppercase;font-size:11px}}
.pair{{display:flex;gap:16px;align-items:center}}.pair>div{{padding:20px;display:flex;align-items:center;justify-content:center;flex:1}}.d{{background:#060607;outline:1px solid #1F1F23}}.l{{background:#EDECE6}}
.word svg{{width:100%;height:auto;max-height:120px}}.icon svg{{width:140px;height:140px}}</style>
<h1>3EDIČ · logo options</h1><div class="grid">{''.join(cells)}</div>"""
    (out / "contact-sheet.html").write_text(html)
    print("wrote", len(list(out.glob('*.svg'))), "svgs + contact-sheet.html")


if __name__ == "__main__":
    main(sys.argv[1])
