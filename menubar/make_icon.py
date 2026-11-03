#!/usr/bin/env python3
"""生成 English Coach 的图标：App 图标 + 菜单栏模板图标。"""
import os
import subprocess

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "icons")
os.makedirs(OUT, exist_ok=True)

BG_TOP = (22, 25, 30)
BG_BOT = (10, 11, 14)
LIME = (200, 255, 77)
CYAN = (94, 234, 212)
SS = 4  # 超采样倍数


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient(size, c1, c2):
    img = Image.new("RGB", (size, size))
    px = img.load()
    n = size - 1
    for y in range(size):
        for x in range(size):
            px[x, y] = lerp(c1, c2, (x + y) / (2 * n))
    return img


def rounded_mask(size, radius):
    big = size * SS
    m = Image.new("L", (big, big), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, big - 1, big - 1], radius=radius * SS, fill=255)
    return m.resize((size, size), Image.LANCZOS)


def draw_icon(size=1024):
    S = size
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    mask = rounded_mask(S, int(S * 0.225))

    # 底板渐变
    img.paste(gradient(S, BG_TOP, BG_BOT), (0, 0), mask)

    # 左上柔光
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-S * 0.30, -S * 0.50, S * 0.62, S * 0.30], fill=(*LIME, 34))
    glow = glow.filter(ImageFilter.GaussianBlur(S * 0.20))
    img.alpha_composite(Image.composite(glow, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask))

    # 右下紫色柔光，增加层次
    glow2 = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow2).ellipse([S * 0.35, S * 0.45, S * 1.25, S * 1.35], fill=(167, 139, 250, 30))
    glow2 = glow2.filter(ImageFilter.GaussianBlur(S * 0.22))
    img.alpha_composite(Image.composite(glow2, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask))

    # 细描边
    border = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    w = max(2, round(S * 0.005))
    ImageDraw.Draw(border).rounded_rectangle(
        [w, w, S - 1 - w, S - 1 - w], radius=int(S * 0.225),
        outline=(255, 255, 255, 30), width=w)
    img.alpha_composite(border)

    # ---- 主体：几何化 E + 句点，超采样绘制 ----
    big = S * SS
    gmask = Image.new("L", (big, big), 0)
    gm = ImageDraw.Draw(gmask)

    pad = S * 0.255
    stroke = S * 0.082
    left = pad
    right = S - pad - S * 0.115   # 右侧留出句点空间
    top = S * 0.315
    bottom = S * 0.685
    cy = (top + bottom) / 2
    mid_right = left + (right - left) * 0.70

    def L(x1, y1, x2, y2):
        gm.line([(x1 * SS, y1 * SS), (x2 * SS, y2 * SS)],
                fill=255, width=round(stroke * SS))

    L(left, top, right, top)
    L(left, cy, mid_right, cy)
    L(left, bottom, right, bottom)
    L(left, top, left, bottom)

    # 端点圆角（line 的 cap 在 PIL 里是方头，手动补圆）
    r = stroke / 2
    for (x, y) in [(left, top), (right, top), (mid_right, cy), (left, cy),
                   (right, bottom), (left, bottom)]:
        gm.ellipse([(x - r) * SS, (y - r) * SS, (x + r) * SS, (y + r) * SS], fill=255)

    # 句点
    dot_cx = right + S * 0.108
    dot_cy = bottom
    dot_r = stroke * 0.62
    gm.ellipse([(dot_cx - dot_r) * SS, (dot_cy - dot_r) * SS,
                (dot_cx + dot_r) * SS, (dot_cy + dot_r) * SS], fill=255)

    gmask = gmask.resize((S, S), Image.LANCZOS)
    grad = gradient(S, LIME, CYAN).convert("RGBA")
    img.alpha_composite(Image.composite(grad, Image.new("RGBA", (S, S), (0, 0, 0, 0)), gmask))

    return img


def draw_template(size):
    """菜单栏模板图：纯黑 + alpha，系统自动反色适配深浅菜单栏。"""
    S = size
    big = S * SS
    m = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(m)

    pad = S * 0.13
    stroke = S * 0.135
    left, right = pad, S - pad - S * 0.10
    top, bottom = S * 0.26, S * 0.74
    cy = (top + bottom) / 2
    mid_right = left + (right - left) * 0.68
    r = stroke / 2

    def L(x1, y1, x2, y2):
        d.line([(x1 * SS, y1 * SS), (x2 * SS, y2 * SS)], fill=255, width=round(stroke * SS))

    L(left, top, right, top)
    L(left, cy, mid_right, cy)
    L(left, bottom, right, bottom)
    L(left, top, left, bottom)
    for (x, y) in [(left, top), (right, top), (mid_right, cy), (left, cy), (right, bottom), (left, bottom)]:
        d.ellipse([(x - r) * SS, (y - r) * SS, (x + r) * SS, (y + r) * SS], fill=255)

    dr = stroke * 0.62
    cx = right + S * 0.095
    d.ellipse([(cx - dr) * SS, (bottom - dr) * SS, (cx + dr) * SS, (bottom + dr) * SS], fill=255)

    m = m.resize((S, S), Image.LANCZOS)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.putalpha(m)
    black = Image.new("RGBA", (S, S), (0, 0, 0, 255))
    out.paste(black, (0, 0), m)
    return out


def main():
    base = draw_icon(1024)
    base.save(os.path.join(OUT, "icon_1024.png"))

    iconset = os.path.join(OUT, "AppIcon.iconset")
    os.makedirs(iconset, exist_ok=True)
    for px, scale in [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2),
                      (256, 1), (256, 2), (512, 1), (512, 2)]:
        name = f"icon_{px}x{px}{'@2x' if scale == 2 else ''}.png"
        base.resize((px * scale, px * scale), Image.LANCZOS).save(os.path.join(iconset, name))

    icns = os.path.join(OUT, "AppIcon.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns], check=True)
    print("生成:", icns)

    draw_template(18).save(os.path.join(OUT, "menubarTemplate.png"))
    draw_template(36).save(os.path.join(OUT, "menubarTemplate@2x.png"))
    print("生成菜单栏模板图标")


if __name__ == "__main__":
    main()
