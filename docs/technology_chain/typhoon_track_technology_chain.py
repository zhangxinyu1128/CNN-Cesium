"""Generate the current typhoon track prediction technology chain.

Outputs (A4 landscape):
  - typhoon_track_technology_chain.png  (300 dpi raster)
  - typhoon_track_technology_chain.svg  (editable vector source)
  - typhoon_track_technology_chain.pdf  (print-ready PDF)

The diagram mirrors the current project contract: the online predictor uses
12 six-hour local-motion steps and a physics-constrained multiscale residual
CNN. ERA5 is shown as an environmental reference / historical experiment,
not as a required online input.
"""

from __future__ import annotations

import html
import math
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont


OUT_DIR = Path(__file__).resolve().parent
PNG_PATH = OUT_DIR / "typhoon_track_technology_chain.png"
SVG_PATH = OUT_DIR / "typhoon_track_technology_chain.svg"
PDF_PATH = OUT_DIR / "typhoon_track_technology_chain.pdf"

# A4 landscape at 300 dpi.
DPI = 300
MM = DPI / 25.4
WIDTH = round(297 * MM)
HEIGHT = round(210 * MM)
FIVE_PT = 10.5


def px_from_pt(points: float) -> int:
    return round(points * DPI / 72)


def load_font(size_pt: float, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
    ]
    if bold:
        candidates = [Path(r"C:\Windows\Fonts\simhei.ttf"), *candidates]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), px_from_pt(size_pt))
    return ImageFont.load_default()


FONT = load_font(FIVE_PT)
FONT_BOLD = load_font(FIVE_PT, bold=True)
TITLE_FONT = load_font(15, bold=True)
SUBTITLE_FONT = load_font(9.5)
SMALL_FONT = load_font(8.5)


BG = "#F7FAFD"
INK = "#18324A"
MUTED = "#547084"
MAIN_FILL = "#E8F2FC"
MAIN_STROKE = "#2B73B6"
MODEL_FILL = "#EAF7F1"
MODEL_STROKE = "#238A62"
SUPPORT_FILL = "#FFF4E4"
SUPPORT_STROKE = "#C8842B"
CAL_FILL = "#F0ECFA"
CAL_STROKE = "#7655A7"
ARROW = "#58758C"
GRID = "#D7E3EC"


def rgba(hex_color: str) -> Tuple[int, int, int, int]:
    value = hex_color.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4)) + (255,)


def draw_centered(draw: ImageDraw.ImageDraw, xy: Tuple[int, int], lines: Sequence[str], font: ImageFont.FreeTypeFont, fill: str, gap: int = 10) -> None:
    x, y = xy
    heights = [draw.textbbox((0, 0), line, font=font)[3] for line in lines]
    total = sum(heights) + gap * (len(lines) - 1)
    cursor = y - total // 2
    for line, height in zip(lines, heights):
        box = draw.textbbox((0, 0), line, font=font)
        draw.text((x - (box[2] - box[0]) // 2, cursor), line, font=font, fill=fill)
        cursor += height + gap


def rounded_node(draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], lines: Sequence[str], fill: str, stroke: str, width: int = 5, font: ImageFont.FreeTypeFont = FONT) -> None:
    draw.rounded_rectangle(box, radius=28, fill=rgba(fill), outline=rgba(stroke), width=width)
    draw_centered(draw, ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), lines, font, INK, gap=12)


def arrow(draw: ImageDraw.ImageDraw, start: Tuple[int, int], end: Tuple[int, int], color: str = ARROW, dashed: bool = False) -> None:
    x1, y1 = start
    x2, y2 = end
    if dashed:
        length = math.hypot(x2 - x1, y2 - y1)
        steps = max(1, int(length / 26))
        for index in range(0, steps, 2):
            a = index / steps
            b = min((index + 1) / steps, 1)
            draw.line((x1 + (x2 - x1) * a, y1 + (y2 - y1) * a, x1 + (x2 - x1) * b, y1 + (y2 - y1) * b), fill=rgba(color), width=4)
    else:
        draw.line((x1, y1, x2, y2), fill=rgba(color), width=6)
    angle = math.atan2(y2 - y1, x2 - x1)
    size = 20
    points = [
        (x2, y2),
        (x2 - size * math.cos(angle - math.pi / 6), y2 - size * math.sin(angle - math.pi / 6)),
        (x2 - size * math.cos(angle + math.pi / 6), y2 - size * math.sin(angle + math.pi / 6)),
    ]
    draw.polygon(points, fill=rgba(color))


def svg_text(x: float, y: float, lines: Sequence[str], size: float = FIVE_PT, weight: str = "400", anchor: str = "middle", fill: str = INK) -> str:
    spans = []
    for index, line in enumerate(lines):
        dy = "0" if index == 0 else "1.35em"
        spans.append(f'<tspan x="{x:.1f}" dy="{dy}">{html.escape(line)}</tspan>')
    return f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-size="{size}pt" font-weight="{weight}" fill="{fill}">{"".join(spans)}</text>'


def create_svg() -> str:
    # Coordinates are expressed in millimetres so the SVG prints at A4 size.
    nodes = [
        (10, 56, 37, 30, ["历史台风观测", "经度 · 纬度", "风速/气压/强度"], MAIN_FILL, MAIN_STROKE),
        (50, 56, 37, 30, ["质控与时间对齐", "缺测 · 异常检查", "6 h 重采样 · 排序"], MAIN_FILL, MAIN_STROKE),
        (90, 56, 37, 30, ["局地运动特征", "东向/北向位移", "速度 · 加速度", "转向率/航向编码"], MAIN_FILL, MAIN_STROKE),
        (130, 56, 37, 30, ["局地运动先验", "匀速外推", "ΔE · ΔN 基线"], MAIN_FILL, MAIN_STROKE),
        (170, 56, 37, 30, ["物理约束多尺度", "残差 CNN", "多尺度因果卷积", "时间注意力", "物理约束损失"], MODEL_FILL, MODEL_STROKE),
        (210, 56, 37, 30, ["未来位移与风速", "预测 ΔE · ΔN", "分时效预测输出"], MAIN_FILL, MAIN_STROKE),
        (250, 56, 37, 30, ["地理坐标重建", "东—北位移→经纬度", "路径 · 风速 · 校准范围"], MAIN_FILL, MAIN_STROKE),
    ]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="297mm" height="210mm" viewBox="0 0 297 210">',
        '<defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 z" fill="#58758C"/></marker></defs>',
        f'<rect width="297" height="210" fill="{BG}"/>',
        '<path d="M12 43H285" stroke="#D7E3EC" stroke-width="0.35"/>',
        svg_text(148.5, 16, ["当前台风路径预测总体技术链路"], size=15, weight="700"),
        svg_text(148.5, 28, ["在线模型：multiscale-residual-physics-12step-seed2028-v1  ·  输入 13 个连续六小时观测  ·  输出 6–36 h"], size=9.5, fill=MUTED),
    ]
    for index, (x, y, w, h, lines, fill, stroke) in enumerate(nodes):
        if index:
            prev_x, prev_y, prev_w, prev_h, *_ = nodes[index - 1]
            parts.append(f'<path d="M{prev_x + prev_w} {prev_y + prev_h / 2} H{x - 1.5}" fill="none" stroke="{ARROW}" stroke-width="0.8" marker-end="url(#arrow)"/>')
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="{fill}" stroke="{stroke}" stroke-width="0.8"/>')
        first_baseline = y + h / 2 - (len(lines) - 1) * 5 / 2
        parts.append(svg_text(x + w / 2, first_baseline, lines, size=FIVE_PT, weight="400"))

    # Supporting branches: intentionally separated from the online input path.
    parts.extend([
        '<rect x="20" y="122" width="112" height="31" rx="3" fill="#FFF4E4" stroke="#C8842B" stroke-width="0.8"/>',
        svg_text(76, 132, ["ERA5 500 / 850 hPa 环境参照", "沿轨风场匹配 · 环境背景", "历史融合实验 / 消融验证"], size=FIVE_PT),
        '<path d="M132 137 H196 V86" fill="none" stroke="#C8842B" stroke-width="0.7" stroke-dasharray="3 2" marker-end="url(#arrow)"/>',
        '<rect x="145" y="122" width="132" height="31" rx="3" fill="#F0ECFA" stroke="#7655A7" stroke-width="0.8"/>',
        svg_text(211, 132, ["验证台风分组历史误差校准", "split-conformal 位置半径", "目标覆盖率 90% · 结果解释"], size=FIVE_PT),
        '<path d="M196 122 V86" fill="none" stroke="#7655A7" stroke-width="0.7" stroke-dasharray="3 2" marker-end="url(#arrow)"/>',
        svg_text(148.5, 171, ["支撑信息不改变在线输入契约：ERA5 用于环境背景/离线实验，历史校准用于不确定性范围显示。"], size=8.5, fill=MUTED),
        '<rect x="20" y="181" width="257" height="15" rx="2" fill="#FFFFFF" stroke="#D7E3EC" stroke-width="0.5"/>',
        svg_text(148.5, 190, ["核心输出：未来 6、12、18、24、30、36 小时台风路径、风速及历史校准范围"], size=FIVE_PT, weight="700"),
        '</svg>',
    ])
    return "\n".join(parts)


def create_png() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    margin = round(18 * MM)
    draw.line((round(12 * MM), round(43 * MM), round(285 * MM), round(43 * MM)), fill=GRID, width=2)
    draw.text((WIDTH // 2, round(7 * MM)), "当前台风路径预测总体技术链路", font=TITLE_FONT, fill=INK, anchor="ma")
    draw.text((WIDTH // 2, round(18 * MM)), "在线模型：multiscale-residual-physics-12step-seed2028-v1  ·  输入 13 个连续六小时观测  ·  输出 6–36 h", font=SUBTITLE_FONT, fill=MUTED, anchor="ma")

    nodes = [
        (10, 56, 37, 30, ["历史台风观测", "经度 · 纬度", "风速/气压/强度"], MAIN_FILL, MAIN_STROKE),
        (50, 56, 37, 30, ["质控与时间对齐", "缺测 · 异常检查", "6 h 重采样 · 排序"], MAIN_FILL, MAIN_STROKE),
        (90, 56, 37, 30, ["局地运动特征", "东向/北向位移", "速度 · 加速度", "转向率/航向编码"], MAIN_FILL, MAIN_STROKE),
        (130, 56, 37, 30, ["局地运动先验", "匀速外推", "ΔE · ΔN 基线"], MAIN_FILL, MAIN_STROKE),
        (170, 56, 37, 30, ["物理约束多尺度", "残差 CNN", "多尺度因果卷积", "时间注意力", "物理约束损失"], MODEL_FILL, MODEL_STROKE),
        (210, 56, 37, 30, ["未来位移与风速", "预测 ΔE · ΔN", "分时效预测输出"], MAIN_FILL, MAIN_STROKE),
        (250, 56, 37, 30, ["地理坐标重建", "东—北位移→经纬度", "路径 · 风速 · 校准范围"], MAIN_FILL, MAIN_STROKE),
    ]
    scale = MM
    for index, (x, y, w, h, lines, fill, stroke) in enumerate(nodes):
        box = (round(x * scale), round(y * scale), round((x + w) * scale), round((y + h) * scale))
        if index:
            prev = nodes[index - 1]
            start = (round((prev[0] + prev[2]) * scale), round((prev[1] + prev[3] / 2) * scale))
            end = (round((x - 1.5) * scale), round((y + h / 2) * scale))
            arrow(draw, start, end)
        rounded_node(draw, box, lines, fill, stroke, width=5)

    # Supporting cards and dashed connectors.
    era5_box = (round(20 * scale), round(122 * scale), round(132 * scale), round(153 * scale))
    cal_box = (round(145 * scale), round(122 * scale), round(277 * scale), round(153 * scale))
    rounded_node(draw, era5_box, ["ERA5 500 / 850 hPa 环境参照", "沿轨风场匹配 · 环境背景", "历史融合实验 / 消融验证"], SUPPORT_FILL, SUPPORT_STROKE, width=4)
    rounded_node(draw, cal_box, ["验证台风分组历史误差校准", "split-conformal 位置半径", "目标覆盖率 90% · 结果解释"], CAL_FILL, CAL_STROKE, width=4)
    arrow(draw, (era5_box[2], (era5_box[1] + era5_box[3]) // 2), (round(196 * scale), round(86 * scale)), SUPPORT_STROKE, dashed=True)
    arrow(draw, (round(196 * scale), cal_box[1]), (round(196 * scale), round(86 * scale)), CAL_STROKE, dashed=True)
    draw.text((WIDTH // 2, round(166 * scale)), "支撑信息不改变在线输入契约：ERA5 用于环境背景/离线实验，历史校准用于不确定性范围显示。", font=SMALL_FONT, fill=MUTED, anchor="ma")
    footer = (round(20 * scale), round(181 * scale), round(277 * scale), round(196 * scale))
    draw.rounded_rectangle(footer, radius=14, fill="white", outline=rgba(GRID), width=2)
    draw.text((WIDTH // 2, round(187 * scale)), "核心输出：未来 6、12、18、24、30、36 小时台风路径、风速及历史校准范围", font=FONT_BOLD, fill=INK, anchor="ma")
    return image


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SVG_PATH.write_text(create_svg(), encoding="utf-8")
    image = create_png()
    image.save(PNG_PATH, dpi=(DPI, DPI), optimize=True)
    image.save(PDF_PATH, "PDF", resolution=DPI)
    print(f"wrote {PNG_PATH}")
    print(f"wrote {SVG_PATH}")
    print(f"wrote {PDF_PATH}")


if __name__ == "__main__":
    main()
