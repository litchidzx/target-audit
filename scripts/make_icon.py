"""生成应用图标。

    python scripts/make_icon.py                 # 输出候选方案供挑选
    python scripts/make_icon.py --export a      # 把某个方案导出成 ico / png

图标是唯一需要在 16×16 像素下仍然可辨认的图形，所以设计上刻意保守：
大色块、粗线条、不用文字、不用细描边。所有图形先按 4 倍尺寸绘制再降采样，
这样边缘才有抗锯齿效果（PIL 本身不做抗锯齿）。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets" / "icons"

SCALE = 4              # 超采样倍数
SIZE = 256             # 最终尺寸
BIG = SIZE * SCALE

# 与站点一致的配色
BLUE_DARK = (18, 52, 140)
BLUE = (31, 79, 209)
WHITE = (255, 255, 255)
GREEN = (16, 145, 88)
DEEP = (13, 22, 44)


def rounded_background(color_a=BLUE, color_b=BLUE_DARK, radius_ratio=0.22) -> Image.Image:
    """带竖向渐变的圆角方形底。"""
    img = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    gradient = Image.new("RGBA", (BIG, BIG))
    draw = ImageDraw.Draw(gradient)
    for y in range(BIG):
        t = y / (BIG - 1)
        draw.line(
            [(0, y), (BIG, y)],
            fill=tuple(round(a + (b - a) * t) for a, b in zip(color_a, color_b)) + (255,),
        )
    mask = Image.new("L", (BIG, BIG), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, BIG - 1, BIG - 1], radius=int(BIG * radius_ratio), fill=255
    )
    img.paste(gradient, (0, 0), mask)
    return img


def draw_target(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float,
                ring_width: float, dot_ratio: float = 0.26, color=WHITE) -> None:
    """靶环：一个粗圆环 + 中心实心点。这是"靶点"最直接的视觉符号。"""
    draw.ellipse(
        [cx - r, cy - r, cx + r, cy + r],
        outline=color,
        width=int(ring_width),
    )
    d = r * dot_ratio
    draw.ellipse([cx - d, cy - d, cx + d, cy + d], fill=color)


def draw_crosshair(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float,
                   gap: float, length: float, width: float, color=WHITE) -> None:
    """靶环外的四条刻度线，强化"瞄准/定位"的语义。"""
    w = int(width)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        draw.line(
            [
                (cx + dx * (r + gap), cy + dy * (r + gap)),
                (cx + dx * (r + gap + length), cy + dy * (r + gap + length)),
            ],
            fill=color,
            width=w,
        )


def variant_a() -> Image.Image:
    """靶环 + 刻度线 + 一道绿色勾。语义最完整，也最像"定位与核验"。"""
    img = rounded_background()
    draw = ImageDraw.Draw(img)
    cx = cy = BIG * 0.47
    r = BIG * 0.215
    draw_target(draw, cx, cy, r, BIG * 0.052)
    draw_crosshair(draw, cx, cy, r, BIG * 0.035, BIG * 0.075, BIG * 0.048)
    # 右下角的勾，用粗线段画
    w = int(BIG * 0.058)
    draw.line(
        [(BIG * 0.60, BIG * 0.745), (BIG * 0.685, BIG * 0.825), (BIG * 0.855, BIG * 0.625)],
        fill=GREEN,
        width=w,
        joint="curve",
    )
    return img


def variant_b() -> Image.Image:
    """纯靶环，无外挂元素。最简洁，16 像素下最清楚。"""
    img = rounded_background()
    draw = ImageDraw.Draw(img)
    cx = cy = BIG * 0.5
    r = BIG * 0.28
    draw_target(draw, cx, cy, r, BIG * 0.070, dot_ratio=0.22)
    draw_crosshair(draw, cx, cy, r, BIG * 0.048, BIG * 0.085, BIG * 0.062)
    return img


def variant_c() -> Image.Image:
    """盾牌 + 靶环。强调"审计"的保护语义，但形状更复杂。"""
    img = rounded_background()
    draw = ImageDraw.Draw(img)
    shield = [
        (BIG * 0.5, BIG * 0.14),
        (BIG * 0.85, BIG * 0.29),
        (BIG * 0.85, BIG * 0.55),
        (BIG * 0.5, BIG * 0.87),
        (BIG * 0.15, BIG * 0.55),
        (BIG * 0.15, BIG * 0.29),
    ]
    draw.polygon(shield, outline=WHITE, width=int(BIG * 0.045))
    draw_target(draw, BIG * 0.5, BIG * 0.48, BIG * 0.175, BIG * 0.045, dot_ratio=0.28)
    return img


def variant_d() -> Image.Image:
    """浅色底 + 蓝色靶环 + 绿色勾。与常见医疗类应用观感接近。"""
    img = rounded_background((248, 250, 253), (223, 232, 246))
    draw = ImageDraw.Draw(img)
    cx, cy = BIG * 0.46, BIG * 0.46
    r = BIG * 0.225
    draw_target(draw, cx, cy, r, BIG * 0.062, color=BLUE)
    draw_crosshair(draw, cx, cy, r, BIG * 0.035, BIG * 0.072, BIG * 0.052, color=BLUE)
    w = int(BIG * 0.058)
    draw.line(
        [(BIG * 0.60, BIG * 0.745), (BIG * 0.685, BIG * 0.825), (BIG * 0.855, BIG * 0.625)],
        fill=GREEN,
        width=w,
        joint="curve",
    )
    return img


VARIANTS = {
    "a": ("靶环 + 刻度 + 绿勾", variant_a),
    "b": ("纯靶环刻度", variant_b),
    "c": ("盾牌 + 靶环", variant_c),
    "d": ("浅底蓝环绿勾", variant_d),
}


def render(name: str) -> Image.Image:
    img = VARIANTS[name][1]()
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def export(name: str) -> list[Path]:
    """导出该方案的全部产物：多尺寸 ico 与常用尺寸 png。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    img = VARIANTS[name][1]()

    ico_path = ROOT / "assets" / "app.ico"
    img.resize((256, 256), Image.LANCZOS).save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    written = [ico_path]
    # favicon：网页与应用窗口都用它。Edge 的应用模式窗口正是拿 favicon 当窗口图标，
    # 所以这一张图决定了任务栏里显示什么（之前没有它，系统就给了个默认地球）。
    favicon = ROOT / "tsa" / "webassets" / "favicon.png"
    img.resize((64, 64), Image.LANCZOS).save(favicon)
    written.append(favicon)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成应用图标")
    parser.add_argument("--export", help="导出指定方案（a/b/c/d）")
    parser.add_argument("--out-dir", default=str(OUT_DIR / "candidates"))
    args = parser.parse_args(argv)

    if args.export:
        if args.export not in VARIANTS:
            print(f"未知方案 {args.export}，可选：{', '.join(VARIANTS)}")
            return 1
        for p in export(args.export):
            print(f"  已生成 {p.relative_to(ROOT)}  ({p.stat().st_size / 1024:.1f} KB)")
        return 0

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # 拼一张对比图，顺便看看缩到 32 像素后还能不能认出来
    preview = Image.new("RGBA", (SIZE * len(VARIANTS), SIZE + 90), (245, 247, 250, 255))
    draw = ImageDraw.Draw(preview)
    for i, (key, (label, _)) in enumerate(VARIANTS.items()):
        img = render(key)
        preview.paste(img, (i * SIZE, 0), img)
        small = img.resize((32, 32), Image.LANCZOS)
        preview.paste(small, (i * SIZE + 112, SIZE + 34), small)
        draw.text((i * SIZE + 8, SIZE + 10), f"{key}  {label}", fill=(40, 48, 64))
        img.save(out / f"{key}.png")
    path = out / "preview.png"
    preview.save(path)
    print(f"已生成候选对比图 {path}")
    print("  上方 256×256，下方是缩到 32 像素后的样子")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
