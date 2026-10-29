"""纯 Python 生成应用图标 bolt.ico（多尺寸 PNG 内嵌 ICO，无第三方依赖）。"""
import struct
import zlib

BASE = 256
BOLT = [(0.575, 0.075), (0.285, 0.545), (0.455, 0.545), (0.395, 0.930),
        (0.715, 0.425), (0.525, 0.425)]
BG_TOP = (32, 38, 52)      # 深灰蓝
BG_BOT = (18, 22, 32)
ACCENT = (76, 141, 255)    # 亮蓝
ACCENT_HI = (140, 185, 255)


def inside_poly(x, y, poly):
    n = len(poly)
    ins = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            ins = not ins
        j = i
    return ins


def render(size):
    """返回 size*size 的 RGBA 像素行列表。"""
    r = 0.20
    px = []
    for y in range(size):
        row = []
        ny = (y + 0.5) / size
        for x in range(size):
            nx = (x + 0.5) / size
            # 圆角矩形遮罩
            cx = min(max(nx, r), 1 - r)
            cy = min(max(ny, r), 1 - r)
            dx, dy = nx - cx, ny - cy
            if dx * dx + dy * dy > r * r and (nx < r or nx > 1 - r) and (ny < r or ny > 1 - r):
                row.append((0, 0, 0, 0))
                continue
            if nx < 0 or nx > 1 or ny < 0 or ny > 1:
                row.append((0, 0, 0, 0))
                continue
            # 背景垂直渐变
            t = ny
            col = tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3))
            # 闪电
            if inside_poly(nx, ny, BOLT):
                k = (nx + ny) / 2
                col = tuple(int(ACCENT_HI[i] * (1 - k) * 0.55 + ACCENT[i] * (1 - (1 - k) * 0.55))
                            for i in range(3))
            row.append((col[0], col[1], col[2], 255))
        px.append(row)
    return px


def to_png(rows):
    h = len(rows)
    w = len(rows[0])
    raw = bytearray()
    for row in rows:
        raw.append(0)
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def main(out="bolt.ico"):
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = []
    for s in sizes:
        rows = render(s) if s <= 64 else [r for r in render(s)]
        images.append((s, to_png(rows)))
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for s, data in images:
        w = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    with open(out, "wb") as f:
        f.write(header + entries + blobs)
    print("wrote", out, "sizes:", sizes)


if __name__ == "__main__":
    main()
