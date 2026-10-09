# -*- coding: utf-8 -*-
"""Render stage-3 comparison charts with Pillow (no matplotlib dependency)."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WORK = Path(r"D:\project\CNN-Cesium\artifacts\reports\stage3_charts")
FONT_PATH = "C:/Windows/Fonts/msyh.ttc"
AXIS = (222, 228, 234)
TEXT = (60, 70, 80)
TITLE = (16, 42, 67)

LABELS = {
    "persistence": "持续性模型",
    "constant_velocity": "匀速外推模型",
    "track_only": "仅轨迹 CNN",
    "center_500_850": "轨迹 + 中心点 ERA5",
    "ring_500_850": "轨迹 + 外环 ERA5",
    "center_plus_ring": "轨迹 + 中心点 + 外环",
}
COLORS = {
    "persistence": (142, 152, 160),
    "constant_velocity": (212, 158, 66),
    "track_only": (58, 110, 165),
    "center_500_850": (86, 168, 168),
    "ring_500_850": (206, 84, 66),
    "center_plus_ring": (124, 96, 168),
}


def _font(size):
    return ImageFont.truetype(FONT_PATH, size)


def _frame(title, ylabel, xlabel):
    W, H = 1500, 900
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((120, 34), title, fill=TITLE, font=_font(40))
    return img, d, (130, 60, 120, 130)


def _axes(d, box, ymax, ticks, xlabels, ylabel, xlabel):
    left, right, top, bottom = box
    W = 1500
    H = 900
    plot_w, plot_h = W - left - right, H - top - bottom
    for i in range(ticks + 1):
        y = top + plot_h - int(plot_h * i / ticks)
        d.line((left, y, left + plot_w, y), fill=AXIS, width=2)
        d.text((left - 100, y - 15), str(int(ymax * i / ticks)), fill=TEXT, font=_font(24))
    n = len(xlabels)
    xs = [left + int(plot_w * i / max(n - 1, 1)) for i in range(n)]
    for x, lab in zip(xs, xlabels):
        d.text((x - 26, top + plot_h + 22), lab, fill=TEXT, font=_font(26))
    d.text((14, top + plot_h // 2 - 26), ylabel, fill=TEXT, font=_font(26))
    d.text((left, H - 40), xlabel, fill=TEXT, font=_font(26))
    return plot_w, plot_h, xs


def line_chart(path, series, horizons, title, ylabel="路径误差 MAE（千米）"):
    img, d, box = _frame(title, ylabel, "预报时效（小时）")
    left, right, top, bottom = box
    plot_w, plot_h = 1500 - left - right, 900 - top - bottom
    ymax = max(max(values[i] for _, values in series) for i in range(len(horizons)))
    ymax = (int(ymax) // 100 + 1) * 100
    plot_w, plot_h, xs = _axes(d, box, ymax, 6, [f"{h}h" for h in horizons], ylabel, "预报时效（小时）")
    for name, values in series:
        pts = [(xs[i], top + plot_h - int(plot_h * values[i] / ymax)) for i in range(len(horizons))]
        d.line(pts, fill=COLORS[name], width=6)
        for x, y in pts:
            d.ellipse((x - 8, y - 8, x + 8, y + 8), fill=COLORS[name])
    ly = top + 8
    for name, _ in series:
        d.rectangle((left + plot_w - 500, ly, left + plot_w - 470, ly + 20), fill=COLORS[name])
        d.text((left + plot_w - 458, ly - 8), LABELS[name], fill=(40, 50, 60), font=_font(26))
        ly += 48
    img.save(path)


def group_bar(path, groups, title, ylabel="路径误差 MAE（千米）"):
    img, d, box = _frame(title, ylabel, "测试集（112 个台风，1661 个窗口）")
    left, right, top, bottom = box
    plot_w, plot_h = 1500 - left - right, 900 - top - bottom
    ymax = max(max(v for _, v in vals) for _, vals in groups)
    ymax = (int(ymax) // 50 + 1) * 50
    plot_w, plot_h, xs = _axes(d, box, ymax, 5, [g for g, _ in groups], ylabel, "测试集（112 个台风，1661 个窗口）")
    slot = plot_w / len(groups)
    bw = min(150, slot * 0.28)
    legend = []
    for i, (gname, vals) in enumerate(groups):
        cx = xs[i]
        total = len(vals) * bw + (len(vals) - 1) * 16
        x0 = cx - total / 2
        for bi, (vname, v) in enumerate(vals):
            h = int(plot_h * v / ymax)
            x = x0 + bi * (bw + 16)
            d.rectangle((x, top + plot_h - h, x + bw, top + plot_h), fill=COLORS[vname])
            d.text((x + bw / 2 - 24, top + plot_h - h - 34), f"{v:.0f}", fill=TEXT, font=_font(24))
            if not legend:
                legend.append(vname)
    ly = top + 8
    for name in legend:
        d.rectangle((left + plot_w - 500, ly, left + plot_w - 470, ly + 20), fill=COLORS[name])
        d.text((left + plot_w - 458, ly - 8), LABELS[name], fill=(40, 50, 60), font=_font(26))
        ly += 48
    img.save(path)


def render_all(data, horizons, stats):
    WORK.mkdir(parents=True, exist_ok=True)
    keys = ["persistence", "constant_velocity", "track_only", "center_500_850", "ring_500_850", "center_plus_ring"]
    line_chart(
        WORK / "horizon_compare.png",
        [(k, [s["mae"] for s in stats[k]]) for k in keys],
        horizons,
        "各时效路径误差对比（三随机种子均值）",
    )
    group_bar(
        WORK / "variant_bar.png",
        [(f"{h}h", [(k, stats[k][i]["mae"]) for k in ("track_only", "center_500_850", "ring_500_850", "center_plus_ring")]) for i, h in enumerate(horizons)],
        "四个变体在各时效的路径误差",
    )
    group_bar(
        WORK / "improvement_bar.png",
        [(f"{h}h", [("track_only", 100.0 * (stats["track_only"][i]["mae"] - stats["ring_500_850"][i]["mae"]) / stats["track_only"][i]["mae"]),
                   ("center_500_850", 100.0 * (stats["center_500_850"][i]["mae"] - stats["ring_500_850"][i]["mae"]) / stats["center_500_850"][i]["mae"]),
                   ("center_plus_ring", 100.0 * (stats["center_plus_ring"][i]["mae"] - stats["ring_500_850"][i]["mae"]) / stats["center_plus_ring"][i]["mae"])]) for i, h in enumerate(horizons)],
        "外环特征相对三种对照的误差降幅",
        ylabel="误差降幅（%）",
    )
    return WORK / "horizon_compare.png", WORK / "variant_bar.png", WORK / "improvement_bar.png"


def render_official_comparison(path, official):
    """Render the common 12/24/36 h frozen-test comparison with JTWC."""
    shared = official["variants"]["ring_500_850"]["metrics"]["shared_leads"]["by_horizon"]
    track = official["variants"]["track_only"]["metrics"]["shared_leads"]["by_horizon"]
    jtwc = {row["lead_hours"]: row["mae_km"] for row in official["jtwc_official"]["by_horizon"]}
    labels = [row["lead_hours"] for row in shared]
    series = [
        ("track_only", [row["mae_km"] for row in track]),
        ("ring_500_850", [row["mae_km"] for row in shared]),
        ("jtwc_official", [jtwc[h] for h in labels]),
    ]
    original = COLORS.get("jtwc_official")
    COLORS["jtwc_official"] = (35, 130, 86)
    LABELS["jtwc_official"] = "JTWC 官方预报"
    line_chart(path, series, labels, "冻结测试集模型与 JTWC 官方预报对比")
    if original is None:
        COLORS.pop("jtwc_official", None)
    else:
        COLORS["jtwc_official"] = original
    LABELS.pop("jtwc_official", None)
    return path
