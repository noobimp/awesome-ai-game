#!/usr/bin/env python3
"""Draw title-card previews and the README hero images.

The opening entries do not ship a creator screenshot, so the cards are drawn
here. Games submitted later keep the screenshot the author uploaded.
"""

import json
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

CATEGORY_COLORS = {
    "Arcade": ((232, 93, 76), (90, 24, 32)),
    "Platformers": ((61, 139, 253), (16, 36, 84)),
    "Racing": ((26, 163, 122), (12, 48, 40)),
    "Action": ((240, 162, 2), (72, 40, 8)),
    "Collections": ((124, 92, 191), (36, 24, 72)),
    "Community": ((222, 93, 131), (72, 20, 40)),
}


def _chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def encode_png(width, height, rgb):
    raw = bytearray()
    row_bytes = width * 3
    for y in range(height):
        raw.append(0)
        start = y * row_bytes
        raw.extend(rgb[start : start + row_bytes])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _chunk(b"IEND", b"")


class Font:
    def __init__(self, path):
        self.data = Path(path).read_bytes()
        self.tables = {}
        num = struct.unpack_from(">H", self.data, 4)[0]
        for i in range(num):
            tag, _checksum, off, length = struct.unpack_from(">4sIII", self.data, 12 + i * 16)
            self.tables[tag] = (off, length)
        off, _ = self.tables[b"head"]
        self.units = struct.unpack_from(">H", self.data, off + 18)[0]
        self.index_fmt = struct.unpack_from(">h", self.data, off + 50)[0]
        off, _ = self.tables[b"maxp"]
        self.num_glyphs = struct.unpack_from(">H", self.data, off + 4)[0]
        off, _ = self.tables[b"hhea"]
        self.num_hmetrics = struct.unpack_from(">H", self.data, off + 34)[0]
        self._parse_loca()
        self._parse_cmap()

    def _parse_loca(self):
        off, _ = self.tables[b"loca"]
        self.glyf_off = self.tables[b"glyf"][0]
        locs = []
        count = self.num_glyphs + 1
        if self.index_fmt == 0:
            for i in range(count):
                locs.append(struct.unpack_from(">H", self.data, off + i * 2)[0] * 2)
        else:
            for i in range(count):
                locs.append(struct.unpack_from(">I", self.data, off + i * 4)[0])
        self.loca = locs

    def _parse_cmap(self):
        off, _ = self.tables[b"cmap"]
        n = struct.unpack_from(">H", self.data, off + 2)[0]
        chosen = None
        fallback = None
        for i in range(n):
            plat, enc, rel = struct.unpack_from(">HHI", self.data, off + 4 + i * 8)
            start = off + rel
            if (plat, enc) == (3, 1):
                chosen = start
                break
            if (plat, enc) in ((0, 3), (3, 10), (0, 4)) and fallback is None:
                fallback = start
        chosen = chosen or fallback
        if chosen is None:
            raise RuntimeError("no unicode cmap")
        fmt = struct.unpack_from(">H", self.data, chosen)[0]
        self.cmap = {}
        if fmt == 4:
            seg_count = struct.unpack_from(">H", self.data, chosen + 6)[0] // 2
            p = chosen + 14
            ends = [struct.unpack_from(">H", self.data, p + i * 2)[0] for i in range(seg_count)]
            p += seg_count * 2 + 2
            starts = [struct.unpack_from(">H", self.data, p + i * 2)[0] for i in range(seg_count)]
            p += seg_count * 2
            deltas = [struct.unpack_from(">h", self.data, p + i * 2)[0] for i in range(seg_count)]
            p += seg_count * 2
            range_pos = p
            range_offs = [struct.unpack_from(">H", self.data, p + i * 2)[0] for i in range(seg_count)]
            for i in range(seg_count):
                for cp in range(starts[i], ends[i] + 1):
                    if cp == 0xFFFF:
                        continue
                    if range_offs[i] == 0:
                        gid = (cp + deltas[i]) & 0xFFFF
                    else:
                        roff = range_pos + i * 2 + range_offs[i] + (cp - starts[i]) * 2
                        gid = struct.unpack_from(">H", self.data, roff)[0]
                        if gid:
                            gid = (gid + deltas[i]) & 0xFFFF
                    if gid:
                        self.cmap[cp] = gid
        elif fmt == 12:
            groups = struct.unpack_from(">I", self.data, chosen + 12)[0]
            p = chosen + 16
            for _ in range(groups):
                start, end, glyph = struct.unpack_from(">III", self.data, p)
                p += 12
                for cp in range(start, end + 1):
                    self.cmap[cp] = glyph + (cp - start)
        else:
            raise RuntimeError(f"unsupported cmap format {fmt}")

    def advance(self, ch):
        gid = self.cmap.get(ord(ch), 0)
        off, _ = self.tables[b"hmtx"]
        if gid < self.num_hmetrics:
            return struct.unpack_from(">H", self.data, off + gid * 4)[0]
        last = off + (self.num_hmetrics - 1) * 4
        return struct.unpack_from(">H", self.data, last)[0]

    def contours(self, ch):
        gid = self.cmap.get(ord(ch), 0)
        start = self.loca[gid]
        end = self.loca[gid + 1]
        if end <= start:
            return []
        return self._contours(self.glyf_off + start)

    def _contours(self, pos):
        data = self.data
        n = struct.unpack_from(">h", data, pos)[0]
        if n <= 0:
            return []
        pos += 10
        ends = [struct.unpack_from(">H", data, pos + i * 2)[0] for i in range(n)]
        pos += n * 2
        instr = struct.unpack_from(">H", data, pos)[0]
        pos += 2 + instr
        total = ends[-1] + 1
        flags = []
        while len(flags) < total:
            flag = data[pos]
            pos += 1
            flags.append(flag)
            if flag & 8:
                rep = data[pos]
                pos += 1
                flags.extend([flag] * rep)
        xs = []
        x = 0
        for flag in flags:
            if flag & 2:
                dx = data[pos]
                pos += 1
                x += dx if flag & 16 else -dx
            elif flag & 16:
                pass
            else:
                x += struct.unpack_from(">h", data, pos)[0]
                pos += 2
            xs.append(x)
        ys = []
        y = 0
        for flag in flags:
            if flag & 4:
                dy = data[pos]
                pos += 1
                y += dy if flag & 32 else -dy
            elif flag & 32:
                pass
            else:
                y += struct.unpack_from(">h", data, pos)[0]
                pos += 2
            ys.append(y)
        on = [bool(flag & 1) for flag in flags]
        contours = []
        begin = 0
        for end_pt in ends:
            contours.append(self._flatten([(xs[i], ys[i], on[i]) for i in range(begin, end_pt + 1)]))
            begin = end_pt + 1
        return contours

    def _flatten(self, pts, steps=7):
        if not pts or not any(point[2] for point in pts):
            return []
        pts = list(pts)
        if not pts[0][2]:
            if pts[-1][2]:
                pts = [pts[-1]] + pts[:-1]
            else:
                pts = [((pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2, True)] + pts
        expanded = []
        count = len(pts)
        for index, point in enumerate(pts):
            nxt = pts[(index + 1) % count]
            expanded.append(point)
            if not point[2] and not nxt[2]:
                expanded.append(((point[0] + nxt[0]) / 2, (point[1] + nxt[1]) / 2, True))
        ons = [index for index, point in enumerate(expanded) if point[2]]
        out = []
        total = len(expanded)
        for index, start in enumerate(ons):
            end = ons[(index + 1) % len(ons)]
            nxt = (start + 1) % total
            if nxt == end:
                out.append((expanded[start][0], expanded[start][1]))
                continue
            control = expanded[nxt]
            if (not control[2]) and ((nxt + 1) % total == end):
                out.extend(_quad(expanded[start], control, expanded[end], steps))
            else:
                out.append((expanded[start][0], expanded[start][1]))
        if out and out[0] != out[-1]:
            out.append(out[0])
        return out


def _quad(p0, p1, p2, steps):
    points = []
    for step in range(steps + 1):
        t = step / steps
        u = 1 - t
        points.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0], u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return points


def _fill_polys(buf, width, height, polys, color):
    edges = []
    miny = height
    maxy = 0
    for poly in polys:
        for i in range(len(poly) - 1):
            x0, y0 = poly[i]
            x1, y1 = poly[i + 1]
            if y0 == y1:
                continue
            if y0 < y1:
                edges.append((y0, y1, x0, x1, 1))
            else:
                edges.append((y1, y0, x1, x0, -1))
            miny = min(miny, y0, y1)
            maxy = max(maxy, y0, y1)
    if not edges:
        return
    ink = bytes(color)
    y0 = max(0, int(miny))
    y1 = min(height, int(maxy) + 1)
    for py in range(y0, y1):
        y = py + 0.5
        hits = []
        for top, bottom, x_top, x_bottom, direction in edges:
            if top <= y < bottom:
                x = x_top + (y - top) * (x_bottom - x_top) / (bottom - top)
                hits.append((x, direction))
        hits.sort(key=lambda item: item[0])
        wind = 0
        start = None
        row = py * width
        for x, direction in hits:
            previous = wind
            wind += direction
            if previous == 0 and wind != 0:
                start = x
            elif previous != 0 and wind == 0 and start is not None:
                left = max(0, int(start))
                right = min(width, int(x) + 1)
                for px in range(left, right):
                    i = (row + px) * 3
                    buf[i : i + 3] = ink
                start = None


def draw_text(buf, width, height, font, text, x, baseline, size, color):
    scale = size / font.units
    pen = x
    for ch in text:
        contours = font.contours(ch)
        polys = []
        for contour in contours:
            poly = [(pen + px * scale, baseline - py * scale) for px, py in contour]
            if len(poly) >= 4:
                polys.append(poly)
        if polys:
            _fill_polys(buf, width, height, polys, color)
        pen += font.advance(ch) * scale
    return pen


def text_width(font, text, size):
    scale = size / font.units
    return sum(font.advance(ch) * scale for ch in text)


def wrap_text(font, text, size, max_width):
    words = text.split()
    if not words:
        return []
    lines = []
    current = words[0]
    for word in words[1:]:
        trial = current + " " + word
        if text_width(font, trial, size) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _fill_gradient(buf, width, height, top, bottom):
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        row = y * width * 3
        pixel = bytes(color)
        for x in range(width):
            buf[row + x * 3 : row + x * 3 + 3] = pixel


def _rect(buf, width, x, y, w, h, color):
    pixel = bytes(color)
    for py in range(y, y + h):
        row = py * width * 3
        for px in range(x, x + w):
            buf[row + px * 3 : row + px * 3 + 3] = pixel


def cover_image(font, game, width=960, height=540):
    accent, deep = CATEGORY_COLORS.get(game["category"], ((232, 255, 71), (20, 20, 28)))
    buf = bytearray(width * height * 3)
    _fill_gradient(buf, width, height, deep, (12, 12, 18))
    _rect(buf, width, 0, 0, 18, height, accent)
    label = game["category"].upper()
    draw_text(buf, width, height, font, label, 56, 92, 22, accent)
    lines = wrap_text(font, game["name"], 64, width - 120)
    baseline = 220 if len(lines) == 1 else 180
    for line in lines[:3]:
        draw_text(buf, width, height, font, line, 56, baseline, 64, (244, 241, 234))
        baseline += 78
    author = "@" + game["author"].lstrip("@")
    draw_text(buf, width, height, font, author, 56, height - 70, 28, (210, 206, 196))
    if game.get("model"):
        model = game["model"]
        mw = text_width(font, model, 22)
        draw_text(buf, width, height, font, model, width - 56 - mw, height - 68, 22, (176, 172, 162))
    return bytes(buf), width, height


def _thumb(raw, sw, sh, dw, dh):
    out = bytearray(dw * dh * 3)
    for y in range(dh):
        sy = min(sh - 1, y * sh // dh)
        for x in range(dw):
            sx = min(sw - 1, x * sw // dw)
            out[(y * dw + x) * 3 : (y * dw + x) * 3 + 3] = raw[(sy * sw + sx) * 3 : (sy * sw + sx) * 3 + 3]
    return out


def _paste(canvas, cw, raw, w, h, x, y):
    for py in range(h):
        src = py * w * 3
        dst = ((y + py) * cw + x) * 3
        canvas[dst : dst + w * 3] = raw[src : src + w * 3]


def mosaic_image(cards, columns=3, thumb_w=480, thumb_h=270, gap=16, pad=28):
    rows = (len(cards) + columns - 1) // columns
    width = pad * 2 + columns * thumb_w + (columns - 1) * gap
    height = pad * 2 + rows * thumb_h + (rows - 1) * gap
    canvas = bytearray(width * height * 3)
    _fill_gradient(canvas, width, height, (18, 18, 24), (8, 8, 12))
    for index, (raw, sw, sh) in enumerate(cards):
        thumb = _thumb(raw, sw, sh, thumb_w, thumb_h)
        col = index % columns
        row = index // columns
        x = pad + col * (thumb_w + gap)
        y = pad + row * (thumb_h + gap)
        _paste(canvas, width, thumb, thumb_w, thumb_h, x, y)
    return encode_png(width, height, canvas)


def site_banner(font, games):
    width, height = 1280, 720
    buf = bytearray(width * height * 3)
    _fill_gradient(buf, width, height, (16, 16, 22), (10, 10, 14))
    draw_text(buf, width, height, font, "AWESOME AI GAME", 64, 92, 28, (232, 255, 71))
    draw_text(buf, width, height, font, "Open-source HTML games made with AI", 64, 148, 36, (244, 241, 234))
    card_w, card_h = 360, 180
    for index, game in enumerate(games[:6]):
        col = index % 3
        row = index // 3
        x = 64 + col * (card_w + 24)
        y = 210 + row * (card_h + 24)
        accent, deep = CATEGORY_COLORS.get(game["category"], ((232, 255, 71), (20, 20, 28)))
        _rect(buf, width, x, y, card_w, card_h, deep)
        _rect(buf, width, x, y, 8, card_h, accent)
        draw_text(buf, width, height, font, game["name"], x + 24, y + 78, 28, (244, 241, 234))
        draw_text(buf, width, height, font, "@" + game["author"].lstrip("@"), x + 24, y + 130, 18, accent)
    return encode_png(width, height, buf)


def load_games():
    return json.loads((ROOT / "data" / "games.json").read_text())


def main():
    font = Font(FONT_PATH)
    games = load_games()
    cards = []
    shot_dir = ROOT / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    for game in games:
        raw, w, h = cover_image(font, game)
        path = ROOT / game["screenshot"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encode_png(w, h, raw))
        cards.append((raw, w, h))
        ink = sum(1 for i in range(0, len(raw), 3) if raw[i] > 40)
        print(f"{game['id']}: {path.stat().st_size} bytes, lit {ink}")
    assets = ROOT / "assets" / "readme"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "mosaic.png").write_bytes(mosaic_image(cards))
    (assets / "site.png").write_bytes(site_banner(font, games))
    print("wrote mosaic and site banner")


if __name__ == "__main__":
    main()
