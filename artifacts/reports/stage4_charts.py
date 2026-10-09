# -*- coding: utf-8 -*-
"""Render stage-4 conformal region charts with Pillow (no matplotlib dependency)."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WORK = Path(r"D:\project\CNN-Cesium\artifacts\reports\stage4_charts")
FONT_PATH = "C:/Windows/Fonts/msyh.ttc"
AXIS = (222, 228, 234)
TEXT = (60, 70, 80)
TITLE = (16, 42, 67)
TRACK_COLOR = (58, 110, 165)
RING_COLOR = (206, 84, 66)
TARGET = (120, 132, 142)


def _font(size):
    return ImageFont.truetype(FONT_PATH, size)


def _frame(title, ylabel, xlabel, ymax):
    W, H = 1500, 900
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((120, 34), title, fill=TITLE, font=_font(40))
    left, right, top, bottom = 150, 130, 120, 130
    plot_w, plot_h = W - left - right, H - top - bottom
    for i in range(7):
        y = top + plot_h - int(plot_h * i / 6)
        d.line((left, y, left + plot_w, y), fill=AXIS, width=2)
        value = ymax * i / 6
        label = f"{value:.0%}" if "覆盖率" in ylabel else f"{value:.0f}"
        d.text((left - 108, y - 15), label, fill=TEXT, font=_font(24))
    return img, d, left, top, plot_w, plot_h


def _xlabels(d, left, top, plot_w, plot_h, horizons, xlabel):
    n = len(horizons)
    xs = [left + int(plot_w * i / max(n - 1, 1)) for i in range(n)]
    for x, h in zip(xs, horizons):
        d.text((x - 26, top + plot_h + 22), f"{h}h", fill=TEXT, font=_font(26))
    d.text((left, 900 - 40), xlabel, fill=TEXT, font=_font(26))
    return xs


def _ylabel(d, left, top, plot_h, ylabel):
    label = Image.new("RGBA", (plot_h, 40), (255, 255, 255, 0))
    label_draw = ImageDraw.Draw(label)
    label_draw.text((0, 0), ylabel, fill=TEXT, font=_font(26))
    label = label.rotate(90, expand=True)
    d._image.paste(label, (0, top + plot_h // 2 - label.height // 2), label)


def _legend(d, left, plot_w, items):
    ly = 8
    for color, label in items:
        d.rectangle((left + plot_w - 520, ly, left + plot_w - 490, ly + 20), fill=color)
        d.text((left + plot_w - 478, ly - 8), label, fill=(40, 50, 60), font=_font(26))
        ly += 48


def multi_line(path, series, horizons, title, ylabel, ymax, target=None, xlabel="预报时效（小时）"):
    img, d, left, top, plot_w, plot_h = _frame(title, ylabel, xlabel, ymax)
    _ylabel(d, left, top, plot_h, ylabel)
    if target is not None:
        y = top + plot_h - int(plot_h * target / ymax)
        for x in range(left, left + plot_w, 22):
            d.line((x, y, x + 12, y), fill=TARGET, width=3)
        d.text((left + 12, y - 40), f"目标 {target:.0%}", fill=TARGET, font=_font(24))
    xs = _xlabels(d, left, top, plot_w, plot_h, horizons, xlabel)
    for color, values, _ in series:
        pts = [(xs[i], top + plot_h - int(plot_h * values[i] / ymax)) for i in range(len(horizons))]
        d.line(pts, fill=color, width=6)
        for x, y in pts:
            d.ellipse((x - 8, y - 8, x + 8, y + 8), fill=color)
    _legend(d, left, plot_w, [(c, l) for c, _, l in series])
    img.save(path)


def grouped_bar(path, groups, title, ylabel, ymax, xlabel):
    img, d, left, top, plot_w, plot_h = _frame(title, ylabel, xlabel, ymax)
    _ylabel(d, left, top, plot_h, ylabel)
    xs = _xlabels(d, left, top, plot_w, plot_h, [g[0] for g in groups], xlabel)
    slot = plot_w / len(groups)
    bw = min(140, slot * 0.3)
    legend = []
    for i, (gname, bars) in enumerate(groups):
        total = len(bars) * bw + (len(bars) - 1) * 18
        x0 = xs[i] - total / 2
        for bi, (color, value, label) in enumerate(bars):
            h = int(plot_h * value / ymax)
            x = x0 + bi * (bw + 18)
            d.rectangle((x, top + plot_h - h, x + bw, top + plot_h), fill=color)
            if len(bars) <= 2:
                d.text((x + bw / 2 - 22, top + plot_h - h - 34), f"{value:.2f}", fill=TEXT, font=_font(24))
            if label not in [l for _, l in legend]:
                legend.append((color, label))
    _legend(d, left, plot_w, legend)
    img.save(path)


def render_all(track, ring, horizons):
    WORK.mkdir(parents=True, exist_ok=True)
    t_eval = {h["lead_hours"]: h for h in track["evaluation"]["by_horizon"]}
    r_eval = {h["lead_hours"]: h for h in ring["evaluation"]["by_horizon"]}

    major = [(TRACK_COLOR, [t_eval[h]["ellipse_90"]["semi_major_axis_km"] for h in horizons], "在线轨迹 CNN 长半轴"),
             (RING_COLOR, [r_eval[h]["ellipse_90"]["semi_major_axis_km"] for h in horizons], "阶段三外环模型长半轴")]
    minor = [(TRACK_COLOR, [t_eval[h]["ellipse_90"]["semi_minor_axis_km"] for h in horizons], "在线轨迹 CNN 短半轴"),
             (RING_COLOR, [r_eval[h]["ellipse_90"]["semi_minor_axis_km"] for h in horizons], "阶段三外环模型短半轴")]
    multi_line(WORK / "semi_axes.png", major + minor, horizons,
               "90% 校准椭圆长短半轴随时效变化", "半轴长度（千米）", 1200)

    coverage = [(TRACK_COLOR, [t_eval[h]["ellipse_90"]["location_coverage_90"] for h in horizons], "轨迹 CNN 窗口级"),
                ((86, 168, 168), [t_eval[h]["ellipse_90"]["location_storm_coverage_90"] for h in horizons], "轨迹 CNN 台风级"),
                (RING_COLOR, [r_eval[h]["ellipse_90"]["location_coverage_90"] for h in horizons], "外环模型 窗口级"),
                ((212, 158, 66), [r_eval[h]["ellipse_90"]["location_storm_coverage_90"] for h in horizons], "外环模型 台风级")]
    multi_line(WORK / "coverage.png", coverage, horizons,
               "90% 椭圆在冻结测试集上的实际覆盖率", "覆盖率", 1.05, target=0.9)

    energy = [(TRACK_COLOR, [t_eval[h]["energy_score_km"] for h in horizons], "轨迹 CNN"),
              (RING_COLOR, [r_eval[h]["energy_score_km"] for h in horizons], "阶段三外环模型")]
    multi_line(WORK / "energy_score.png", energy, horizons,
               "位置集合 Energy Score 随时效变化", "Energy Score（千米）", 280)

    area = [(TRACK_COLOR, [t_eval[h]["ellipse_90"]["area_km2"] / 1e6 for h in horizons], "轨迹 CNN"),
            (RING_COLOR, [r_eval[h]["ellipse_90"]["area_km2"] / 1e6 for h in horizons], "阶段三外环模型")]
    multi_line(WORK / "area.png", area, horizons,
               "90% 校准椭圆面积随时效变化", "椭圆面积（百万平方千米）", 3.5)

    grouped_bar(
        WORK / "semi_axis_bar.png",
        [(f"{h}h", [(TRACK_COLOR, t_eval[h]["ellipse_90"]["semi_major_axis_km"], "轨迹 CNN 长半轴"),
                     (RING_COLOR, r_eval[h]["ellipse_90"]["semi_major_axis_km"], "外环模型 长半轴"),
                     ((120, 170, 200), t_eval[h]["ellipse_90"]["semi_minor_axis_km"], "轨迹 CNN 短半轴"),
                     ((226, 150, 140), r_eval[h]["ellipse_90"]["semi_minor_axis_km"], "外环模型 短半轴")]) for h in horizons],
        "两套模型的 90% 椭圆长短半轴对比", "半轴长度（千米）", 1200, "预报时效（小时）",
    )

    grouped_bar(
        WORK / "storm_coverage_bar.png",
        [(f"{h}h", [(TRACK_COLOR, t_eval[h]["ellipse_90"]["location_storm_coverage_90"], "轨迹 CNN"),
                     (RING_COLOR, r_eval[h]["ellipse_90"]["location_storm_coverage_90"], "阶段三外环模型")]) for h in horizons],
        "整场台风覆盖率（90% 目标）", "台风级覆盖率", 1.05, "预报时效（小时）",
    )

    return WORK / "semi_axes.png", WORK / "coverage.png", WORK / "energy_score.png", \
        WORK / "area.png", WORK / "semi_axis_bar.png", WORK / "storm_coverage_bar.png"
