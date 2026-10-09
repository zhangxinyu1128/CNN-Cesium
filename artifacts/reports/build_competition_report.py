"""Build a concise, evidence-grounded Chinese competition report as DOCX."""

import json
import statistics
import tempfile
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "artifacts" / "reports"
OUTPUT = REPORTS / "热带气旋轨迹预测训练报告.docx"
BLUE = "164E63"
TEAL = "0F766E"
INK = "172B3A"
MUTED = "526575"
PALE = "EAF2F4"
ALT = "F4F7F8"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def set_run_font(run, size=10, bold=False, color=INK):
    run.font.name = "Microsoft YaHei"
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)


def set_cell_shading(cell, fill):
    properties = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)


def set_cell_text(cell, text, *, header=False, size=8.5, align=WD_ALIGN_PARAGRAPH.CENTER):
    cell.text = ""
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    paragraph = cell.paragraphs[0]
    paragraph.alignment = align
    paragraph.paragraph_format.space_before = Pt(2)
    paragraph.paragraph_format.space_after = Pt(2)
    display_text = str(text).replace("輸入", "输入").replace("運動", "运动").replace("僅", "仅")
    run = paragraph.add_run(display_text)
    set_run_font(run, size=size, bold=header, color="FFFFFF" if header else INK)
    if header:
        set_cell_shading(cell, BLUE)


def repeat_table_header(row):
    properties = row._tr.get_or_add_trPr()
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("w:val"), "true")
    properties.append(repeat)


def add_table(document, headers, rows, widths=None, font_size=8.5):
    table = document.add_table(rows=1, cols=len(headers))
    table.autofit = False
    table.style = "Table Grid"
    repeat_table_header(table.rows[0])
    for idx, header in enumerate(headers):
        set_cell_text(table.rows[0].cells[idx], header, header=True, size=font_size)
        if widths:
            table.rows[0].cells[idx].width = Inches(widths[idx])
    for row_index, values in enumerate(rows):
        cells = table.add_row().cells
        for idx, value in enumerate(values):
            set_cell_text(cells[idx], value, size=font_size)
            if row_index % 2 == 1:
                set_cell_shading(cells[idx], ALT)
            if widths:
                cells[idx].width = Inches(widths[idx])
    document.add_paragraph().paragraph_format.space_after = Pt(1)
    return table


def add_paragraph(document, text, *, size=10, color=INK, bold_prefix=None, after=5):
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(after)
    paragraph.paragraph_format.line_spacing = 1.15
    if bold_prefix and text.startswith(bold_prefix):
        run = paragraph.add_run(bold_prefix)
        set_run_font(run, size=size, bold=True, color=color)
        run = paragraph.add_run(text[len(bold_prefix):])
        set_run_font(run, size=size, color=color)
    else:
        run = paragraph.add_run(text)
        set_run_font(run, size=size, color=color)
    return paragraph


def add_heading(document, text, level=1):
    paragraph = document.add_paragraph(style=f"Heading {level}")
    paragraph.paragraph_format.keep_with_next = True
    paragraph.paragraph_format.space_before = Pt(9 if level == 1 else 6)
    paragraph.paragraph_format.space_after = Pt(4)
    run = paragraph.add_run(text)
    set_run_font(run, size=14 if level == 1 else 11, bold=True, color=BLUE if level == 1 else TEAL)
    return paragraph


def remove_paragraph_borders(properties):
    if properties is None:
        return
    for border in properties.findall(qn("w:pBdr")):
        properties.remove(border)


def load_font(size):
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def make_chart(path, series):
    width, height = 1500, 650
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font, label_font = load_font(32), load_font(22)
    draw.text((90, 24), "固定测试集轨迹误差对比", fill=f"#{INK}", font=title_font)
    left, right, top, bottom = 115, width - 55, 105, height - 100
    maximum = 700.0
    for value in range(0, 701, 100):
        y = bottom - (bottom - top) * value / maximum
        draw.line((left, y, right, y), fill="#DCE5E8", width=2)
        draw.text((48, y - 13), str(value), fill=f"#{MUTED}", font=label_font)
    draw.text((18, 68), "MAE km", fill=f"#{MUTED}", font=label_font)
    leads = [6, 12, 18, 24, 30, 36]
    for idx, lead in enumerate(leads):
        x = left + idx * (right - left) / (len(leads) - 1)
        draw.text((x - 27, bottom + 18), f"{lead}h", fill=f"#{MUTED}", font=label_font)
    colors = {"Persistence": "#9A6700", "匀速外推": "#687782", "原始 CNN": "#1473A8", "残差 CNN": "#0F766E"}
    for name, values in series.items():
        points = []
        for idx, value in enumerate(values):
            x = left + idx * (right - left) / (len(leads) - 1)
            y = bottom - (bottom - top) * value / maximum
            points.append((x, y))
        draw.line(points, fill=colors[name], width=5)
        for x, y in points:
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=colors[name])
    legend_y = height - 39
    start_x = 300
    for name, color in colors.items():
        draw.line((start_x, legend_y + 10, start_x + 35, legend_y + 10), fill=color, width=5)
        draw.text((start_x + 44, legend_y - 5), name, fill=f"#{INK}", font=label_font)
        start_x += 265
    image.save(path, quality=94)


def main():
    manifest = read_json(ROOT / "data" / "processed" / "manifest.json")
    base = read_json(ROOT / "artifacts" / "baseline_frozen" / "runs" / "20260927T121030Z" / "test_metrics.json")
    residual_path = ROOT / "artifacts" / "residual" / "runs" / "20260927T121802Z" / "test_metrics.json"
    residual = read_json(residual_path)
    seed_paths = [
        residual_path,
        ROOT / "artifacts" / "residual_seed_sensitivity" / "seed_2027" / "runs" / "20260928T093146Z" / "test_metrics.json",
        ROOT / "artifacts" / "residual_seed_sensitivity" / "seed_2028" / "runs" / "20260928T094327Z" / "test_metrics.json",
    ]
    seeds = [read_json(path) for path in seed_paths]
    for run in [base, *seeds]:
        if run["dataset_fingerprint_sha256"] != manifest["source"]["fingerprint_sha256"]:
            raise ValueError("training run dataset fingerprint does not match current manifest")

    cnn = base["evaluation"]["cnn"]["by_horizon"]
    persistence = base["evaluation"]["baselines"]["last_observation"]["by_horizon"]
    constant = base["evaluation"]["baselines"]["constant_velocity"]["by_horizon"]
    resid_rows = residual["evaluation"]["cnn_residual"]["by_horizon"]
    horizons = [item["lead_hours"] for item in resid_rows]
    chart_series = {
        "Persistence": [item["path_mae_km"] for item in persistence],
        "匀速外推": [item["path_mae_km"] for item in constant],
        "原始 CNN": [item["path_mae_km"] for item in cnn],
        "残差 CNN": [item["path_mae_km"] for item in resid_rows],
    }

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.62)
    section.bottom_margin = Inches(0.62)
    section.left_margin = Inches(0.68)
    section.right_margin = Inches(0.68)
    normal = doc.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(10)
    for style_name in ("Heading 1", "Heading 2"):
        doc.styles[style_name].font.name = "Microsoft YaHei"
        doc.styles[style_name]._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")

    title = doc.add_paragraph(style="Title")
    title_style = doc.styles["Title"]._element
    remove_paragraph_borders(title_style.pPr)
    remove_paragraph_borders(title._p.pPr)
    title.paragraph_format.space_after = Pt(3)
    title_run = title.add_run("热带气旋轨迹预测模型训练报告")
    set_run_font(title_run, size=22, bold=True, color="000000")
    subtitle = doc.add_paragraph()
    subtitle.paragraph_format.space_after = Pt(7)
    set_run_font(subtitle.add_run("CNN-Cesium 项目竞赛技术材料  |  2026年9月28日"), size=10, color=MUTED)

    add_paragraph(
        doc,
        "结论：在冻结的 2020-2025 年测试集上，匀速运动先验与 CNN 残差修正组合的路径 MAE 在 6-36 小时均低于 Persistence、匀速外推和直接输出轨迹的 CNN。三种随机种子复核方向一致。当前证据来自历史最佳路径数据，尚未接入 ERA5，也没有足够的同起报官方预报样本，因此结论只适用于本测试集。",
        size=10.5,
    )
    add_table(
        doc,
        ["数据范围", "训练 / 验证 / 测试台风", "测试窗口", "核心发现"],
        [["1945-2025", "1,687 / 94 / 154", "1,670", "残差 CNN 六个时效均优于匀速基线"]],
        widths=[1.2, 2.2, 1.05, 2.5],
        font_size=8.5,
    )
    add_heading(doc, "数据质量和适用范围", level=2)
    add_paragraph(
        doc,
        "经纬度字段完整，未发现重复时间戳和非单调时间序列；speed 与 power 各缺失 15 点。pressure 缺失 2,083 点，移动方向、移动速度和风圈半径缺测更多，因此没有作为稳定输入。原始数据中未发现已接入的网格气象场文件。",
        size=9.5,
        after=3,
    )

    add_heading(doc, "数据与训练设置")
    add_paragraph(
        doc,
        "数据集含 1,935 条有效台风轨迹和 73,388 个观测点。按台风编号整场分组，训练年份为 1945-2016，验证年份为 2017-2019，测试年份为 2020-2025；同一台风不会跨集合。预处理将轨迹重采样为 6 小时间隔，归一化统计量仅由训练台风拟合。",
    )
    add_paragraph(
        doc,
        "模型输入为最近 4 个时刻的经度、纬度、风速、强度及经纬度位移，共 6 项特征；直接预测未来 6、12、18、24、30、36 小时的位置和风速。残差模型先按最近运动速度作匀速外推，再由一维卷积网络学习位置与风速修正。每个训练种子最多 60 轮，batch size 为 256，学习率 0.001，Dropout 为 0.3，验证集早停；三次训练均使用 CPU。",
    )
    splits = manifest["preprocessing"]["split_sample_counts"]
    storm_counts = manifest["preprocessing"]["split_file_storm_counts"]
    add_table(
        doc,
        ["集合", "年份", "名单台风数", "有效窗口数"],
        [
            ["训练", "1945-2016", storm_counts["train"], splits["train"]],
            ["验证", "2017-2019", storm_counts["validation"], splits["validation"]],
            ["测试", "2020-2025", storm_counts["test"], splits["test"]],
        ],
        widths=[1.0, 1.3, 1.5, 1.5],
    )

    add_heading(doc, "固定测试集轨迹误差")
    with tempfile.TemporaryDirectory() as temporary:
        chart_path = Path(temporary) / "path_error.png"
        make_chart(chart_path, chart_series)
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.add_run().add_picture(str(chart_path), width=Inches(6.65))
    rows = []
    for idx, horizon in enumerate(horizons):
        rows.append(
            [
                f"{horizon}h",
                f"{persistence[idx]['path_mae_km']:.1f}",
                f"{constant[idx]['path_mae_km']:.1f}",
                f"{cnn[idx]['path_mae_km']:.1f}",
                f"{resid_rows[idx]['path_mae_km']:.1f}",
                f"{100 * (1 - resid_rows[idx]['path_mae_km'] / constant[idx]['path_mae_km']):.1f}%",
            ]
        )
    add_table(
        doc,
        ["时效", "Persistence", "匀速外推", "直接 CNN", "残差 CNN", "相对匀速改善"],
        rows,
        widths=[0.65, 1.05, 1.05, 0.95, 0.95, 1.2],
        font_size=8.2,
    )
    add_paragraph(
        doc,
        "轨迹 MAE 的单位为 km，按 1,670 个测试窗口计算。残差 CNN 相对匀速外推改善 3.1%-12.7%；直接 CNN 在 6-18 小时的误差高于匀速基线，不能把所有 CNN 架构概括为都优于传统方法。",
        size=9.5,
    )

    add_heading(doc, "稳定性与消融")
    stable_rows = []
    for index, horizon in enumerate(horizons):
        path_values = [run["evaluation"]["cnn_residual"]["by_horizon"][index]["path_mae_km"] for run in seeds]
        wind_values = [run["evaluation"]["cnn_residual"]["by_horizon"][index]["wind_mae_ms"] for run in seeds]
        stable_rows.append(
            [
                f"{horizon}h",
                f"{statistics.mean(path_values):.2f} ± {statistics.stdev(path_values):.2f}",
                f"{statistics.mean(wind_values):.2f} ± {statistics.stdev(wind_values):.2f}",
            ]
        )
    add_table(doc, ["时效", "路径 MAE km 均值 ± 标准差", "风速 MAE 源数据单位 均值 ± 标准差"], stable_rows, widths=[0.8, 2.6, 3.4], font_size=8.5)
    add_paragraph(doc, "标准差来自 3 个训练种子，不是置信区间。风速原始字段单位尚缺随数据保存的官方字典；报告中的 m/s 按 CMA 最佳路径字段惯例解释，正式提交前应附来源与单位凭据。", size=9)
    add_paragraph(doc, "10,000 次台风级配对 bootstrap 中，18 个种子-时效区间均排除 0；结论仅适用于本测试集。", size=8.5)

    ablation_heading = add_heading(doc, "轨迹特征消融")
    ablation_heading.paragraph_format.page_break_before = True
    ablation = read_json(ROOT / "artifacts" / "ablation" / "metrics_controlled.json")["variants"]
    abl_rows = []
    for index, horizon in enumerate(horizons):
        vals = [ablation[key]["test"]["by_horizon"][index]["path_mae_km"] for key in ("full", "without_motion", "position_only")]
        abl_rows.append([f"{horizon}h", *[f"{value:.1f}" for value in vals]])
    add_table(doc, ["時效", "完整輸入", "去運動增量", "僅位置"], abl_rows, widths=[0.8, 1.7, 1.7, 1.7])
    add_paragraph(doc, "四種输入设置使用相同随机种子、初始化和批次顺序。去掉经纬度运动增量后，六个时效的路径 MAE 均变差；仅位置输入同样弱于完整输入。此消融证明的是轨迹特征贡献，不是气象场融合贡献。", size=9)

    add_heading(doc, "不确定性与业务预报核查")
    add_paragraph(doc, "已用验证集 69 场台风做台风级 conformal 校准，目标覆盖率为 90%。测试集 114 场台风的整场覆盖率在六个时效为 86.8%-94.7%，部分时效低于 90%；位置半径约 168-994 km，区间较宽。当前结果不等于已校准的二维概率锥体，展示时必须标注测试覆盖率与校准口径。", size=9.5)
    audit = read_json(REPORTS / "operational_forecast_audit.json")
    counts = audit["counts"]
    add_paragraph(
        doc,
        f"原始数据发现 {counts['all_forecast_origins']} 个带业务预报的起报点、{counts['all_forecast_positions']} 个预报位置；冻结测试台风中有 {counts['test_forecast_origins']} 个起报点和 {counts['test_forecast_positions']} 个预报位置，但严格匹配本模型历史输入、支持时效和最佳路径真值后的可比样本数为 {counts['comparable_positions']}。因此本报告不提供官方预报优劣结论。",
        size=9.5,
    )

    add_heading(doc, "结论与待完成事项")
    add_paragraph(doc, "当前可作为项目实测结果的结论是：在该冻结测试集上，匀速先验加 CNN 残差修正降低了六个时效的平均轨迹误差；三种子结果方向一致，轨迹运动增量对位置预测有帮助。结果不证明跨海盆、未来业务场景或其他数据源上的泛化。", size=9.5)
    add_table(
        doc,
        ["待完成项", "当前状态与必要证据"],
        [
            ["气象场融合", "没有 ERA5 文件或 CDS 凭据；取得数据后须做覆盖审计、融合训练和同预算消融。"],
            ["官方预报对照", "现有字段可比样本为 0；需补有来源、产品版本、起报时间和真值的配对预报。"],
            ["概率锥体", "有台风级校准半径，但覆盖率未在所有时效达到 90%，二维锥体尚未完成。"],
            ["数据与单位来源", "提交前补最佳路径下载记录、字段字典、许可信息及 speed 单位引用。"],
        ],
        widths=[1.3, 5.55],
        font_size=8.6,
    )

    add_heading(doc, "复现材料")
    add_paragraph(doc, f"数据指纹：{manifest['source']['fingerprint_sha256']}", size=8.5, color=MUTED, after=2)
    add_paragraph(doc, "固定拆分：data/splits/storm_splits.json；审计清单：data/processed/manifest.json。", size=8.5, color=MUTED, after=2)
    add_paragraph(doc, "训练指标：artifacts/baseline_frozen 与 artifacts/residual；种子复核：artifacts/residual_seed_sensitivity。", size=8.5, color=MUTED, after=2)
    add_paragraph(doc, "消融、bootstrap、不确定性和业务预报审计分别保存在 artifacts/ablation、artifacts/residual、artifacts/reports。完整中文技术记录见 training_report.md。", size=8.5, color=MUTED)

    doc.core_properties.title = "热带气旋轨迹预测模型训练报告"
    doc.core_properties.subject = "CNN-Cesium 项目竞赛训练与验证证据"
    doc.core_properties.author = "CNN-Cesium 项目组"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    main()
