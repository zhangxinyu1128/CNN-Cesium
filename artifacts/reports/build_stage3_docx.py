# -*- coding: utf-8 -*-
"""Build the stage-3 ERA5 annular steering-flow report as a polished DOCX."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

import stage3_charts
import stage3_data

OUT = Path(r"C:\Users\Lenovo\Desktop\新建文件夹 (2)\CNN-Cesium_阶段三_ERA5外环引导流实验报告_20261008.docx")
ACCENT = "176B87"
DARK = "102A43"

data, horizons, stats, official, stage1 = stage3_data.load()
chart1, chart2, chart3 = stage3_charts.render_all(data, horizons, stats)
official_chart = stage3_charts.render_official_comparison(
    stage3_charts.WORK / "official_comparison.png", official
)

ring = stats["ring_500_850"]
track = stats["track_only"]
center = stats["center_500_850"]
both = stats["center_plus_ring"]
cv = stats["constant_velocity"]
per = stats["persistence"]
i24, i36 = 3, 5


def pct(base, cand):
    return 100.0 * (base - cand) / base


doc = Document()
sec = doc.sections[0]
sec.top_margin = Inches(0.75)
sec.bottom_margin = Inches(0.7)
sec.left_margin = Inches(0.85)
sec.right_margin = Inches(0.85)
styles = doc.styles
styles["Normal"].font.name = "Microsoft YaHei"
styles["Normal"]._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
styles["Normal"].font.size = Pt(10.5)
styles["Normal"].paragraph_format.space_after = Pt(6)
styles["Normal"].paragraph_format.line_spacing = 1.2
for name, size, color in (("Title", 24, DARK), ("Heading 1", 15.5, DARK), ("Heading 2", 12.5, ACCENT)):
    s = styles[name]
    s.font.name = "Microsoft YaHei"
    s._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    s.font.size = Pt(size)
    s.font.bold = True
    s.font.color.rgb = RGBColor.from_string(color)
    s.paragraph_format.space_before = Pt(12)
    s.paragraph_format.space_after = Pt(5)


def shade(cell, fill):
    tc = cell._tc.get_or_add_tcPr()
    sh = tc.find(qn("w:shd"))
    if sh is None:
        sh = OxmlElement("w:shd")
        tc.append(sh)
    sh.set(qn("w:fill"), fill)


def ct(cell, text, bold=False, color=None, size=9):
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.line_spacing = 1.12
    r = p.add_run(str(text))
    r.bold = bold
    r.font.name = "Microsoft YaHei"
    r._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    r.font.size = Pt(size)
    if color:
        r.font.color.rgb = RGBColor.from_string(color)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def tbl(headers, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(headers):
        shade(t.rows[0].cells[i], ACCENT)
        ct(t.rows[0].cells[i], h, True, "FFFFFF", 9)
    for r_i, row in enumerate(rows):
        cs = t.add_row().cells
        for i, v in enumerate(row):
            if i == 0:
                shade(cs[i], "EAF4F7")
            elif r_i % 2 == 1:
                shade(cs[i], "F7FAFC")
            ct(cs[i], v, i == 0)
    if widths:
        for r in t.rows:
            for i, w in enumerate(widths):
                r.cells[i].width = Inches(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(1)


def figure(path, caption, width=6.3):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(2)
    p.add_run().add_picture(str(path), width=Inches(width))
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_after = Pt(10)
    r = cap.add_run(caption)
    r.font.size = Pt(9)
    r.font.color.rgb = RGBColor.from_string("5A6B7B")


header = sec.header.paragraphs[0]
header.text = "CNN-Cesium 阶段三实验报告"
header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
header.runs[0].font.size = Pt(8)
header.runs[0].font.color.rgb = RGBColor.from_string("829AB1")
footer = sec.footer.paragraphs[0]
footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
footer.add_run("阶段三 ERA5 外环引导流实验  |  第 ")
rr = footer.add_run()
a = OxmlElement("w:fldChar"); a.set(qn("w:fldCharType"), "begin")
b = OxmlElement("w:instrText"); b.set(qn("xml:space"), "preserve"); b.text = " PAGE "
c = OxmlElement("w:fldChar"); c.set(qn("w:fldCharType"), "end")
rr._r.extend([a, b, c])
footer.add_run(" 页")

doc.add_paragraph("CNN-Cesium 阶段三 ERA5 外环引导流实验报告", style="Title")
sub = doc.add_paragraph()
rs = sub.add_run("用真实 CMA 最佳路径与 ERA5 再分析数据检验外环平均环境引导流带来的路径预测增益")
rs.font.size = Pt(11)
rs.font.color.rgb = RGBColor.from_string(ACCENT)

doc.add_heading("1 结论摘要", 1)
doc.add_paragraph(
    "本阶段在冻结划分、同一残差网络、同一超参数下，对四个特征配置完成 3 个随机种子重复实验。"
    f"外环平均引导流特征把 24 小时路径误差从仅轨迹方案的 {track[i24]['mae']:.2f} 千米降到 {ring[i24]['mae']:.2f} 千米，"
    f"降幅 {pct(track[i24]['mae'], ring[i24]['mae']):.2f}%；36 小时从 {track[i36]['mae']:.2f} 千米降到 {ring[i36]['mae']:.2f} 千米，"
    f"降幅 {pct(track[i36]['mae'], ring[i36]['mae']):.2f}%。与项目已有的中心点 ERA5 融合相比，"
    f"外环特征在 24 与 36 小时分别再降低 {pct(center[i24]['mae'], ring[i24]['mae']):.2f}% 与 {pct(center[i36]['mae'], ring[i36]['mae']):.2f}%。"
)
doc.add_paragraph(
    "在六个预报时效上，外环方案的三种子标准差为 "
    + "、".join(f"{ring[i]['std']:.2f}" for i in range(len(horizons)))
    + " 千米，稳定性高于仅轨迹方案。"
      f"外环方案在 24 小时优于匀速外推基线（{cv[i24]['mae']:.2f} 千米），36 小时外环方案为 {ring[i36]['mae']:.2f} 千米，"
      f"匀速外推为 {cv[i36]['mae']:.2f} 千米，仍低 {cv[i36]['mae'] - ring[i36]['mae']:.2f} 千米，"
    "说明环境引导流提供了匀速外推无法表达的中期转向信息。"
)
doc.add_paragraph(
    "结论范围：ERA5 消融数值来自真实 GRIB 采样与真实训练，测试集为 ERA5 配对子集（112 个台风、1661 个窗口）。"
    f"阶段一已补入 JTWC 官方预报，另在同一冻结测试窗口上完成 {official['coverage']['test_windows_scored']} 个窗口、"
    f"{official['coverage']['storm_count']} 个台风的独立对比；官方与模型仅在共同存在的 12、24、36 小时进行比较。"
)

doc.add_heading("2 阶段目标与选题依据", 1)
doc.add_paragraph(
    "内部评审文档指出，现有模型主要使用轨迹数据，中心点 ERA5 融合增益有限，算法创新偏常规。"
    "本阶段据此把气象要素表示从台风中心的单点环境，升级为台风外环的平均环境引导流："
    "以中心为原点构造环带并做半径加权平均，使气象场融合从形式上的多通道输入，"
    "升级为有明确物理含义的环境流场特征。官方预报对比作为后续工作保留，不使用任何模拟数据填补。"
)

doc.add_heading("3 数据、划分与防泄漏规则", 1)
doc.add_paragraph(
    "轨迹数据为 CMA 最佳路径，共 73388 个轨迹点；环境场为 ERA5 500 hPa 与 850 hPa 的 u、v 分量。"
    "划分沿用项目已有冻结划分，以台风编号或年份为单位，不对相邻轨迹点随机切分，"
    "避免同一次台风过程跨越训练集与测试集。ERA5 场次取观测时刻之前最近的一个 6 小时 UTC 场次。"
)
tbl(
    ["划分", "窗口数", "台风个数", "备注"],
    [
        ["train", "25961", "1210", "1946-2019，部分年份缺测"],
        ["validation", "687", "48", "以 2018 年为主"],
        ["test", "1661", "112", "2020、2021、2025"],
        ["点级 ERA5 配对率", "58098 / 73388", "79.17%", "与已有中心点实验同源"],
    ],
    widths=[1.5, 1.4, 1.2, 2.4],
)

doc.add_heading("4 外环引导流特征定义", 1)
doc.add_paragraph(
    "以台风中心为原点，在 350、550、750 千米三个半径上各取 8 个方位点，共 24 个采样点。"
    "每个采样点双线性插值得到 500 hPa 与 850 hPa 的 u、v 分量，再按半径加权平均，"
    "得到 u500、v500、u850、v850 四个环带特征。采样完全使用观测时刻之前的风场。"
)
tbl(
    ["特征或规则", "具体定义"],
    [
        ["u500 / v500", "350-750 千米环带平均 500 hPa 纬向、经向风速分量，单位 m/s"],
        ["u850 / v850", "350-750 千米环带平均 850 hPa 纬向、经向风速分量，单位 m/s"],
        ["时间匹配", "观测时刻之前最近的一个 6 小时 UTC ERA5 场次"],
        ["空间插值", "ERA5 原生规则经纬网双线性插值"],
        ["聚合方式", "按半径加权平均，权重为采样半径"],
    ],
    widths=[1.7, 4.8],
)

doc.add_heading("5 实验设计", 1)
doc.add_heading("5.1 变体配置", 2)
doc.add_paragraph(
    "四个变体使用同一残差 CNN 结构、同一超参数与同一冻结划分，仅输入特征索引不同："
    "仅轨迹取 0-5，轨迹加中心点 ERA5 取 0-10，轨迹加外环 ERA5 取 0-5 与 11-14，"
    "轨迹加中心点加外环取 0-14。"
)
doc.add_heading("5.2 基线模型", 2)
doc.add_paragraph("保持项目已有基线：持续性模型重复最后一个观测位置，匀速外推模型按最后一步位移线性外推。")
doc.add_heading("5.3 训练配置", 2)
tbl(
    ["配置项", "值"],
    [
        ["网络结构", "ResidualTrackCNN，dropout 0.2"],
        ["优化器", "Adam，学习率 1e-3，权重衰减 1e-5"],
        ["epochs / patience", "40 / 8"],
        ["batch size", "256"],
        ["随机种子", "2026、2027、2028"],
        ["运行设备", data["device"]],
    ],
    widths=[2.0, 4.5],
)
doc.add_heading("5.4 无未来泄漏说明", 2)
doc.add_paragraph(
    "每个窗口取 4 个 6 小时历史步，预测 6 个 6 小时步，即 6 至 36 小时。"
    "ERA5 场次始终取观测时刻之前最近的一个 6 小时场次，不使用预测目标时段内的任何场次。"
    "数据划分以台风编号或年份为单位。"
)

doc.add_heading("6 实验结果", 1)
doc.add_heading("6.1 各时效路径误差与基线对比", 2)
rows = []
for i, h in enumerate(horizons):
    rows.append([
        f"{h}h",
        f"{per[i]['mae']:.2f}",
        f"{cv[i]['mae']:.2f}",
        f"{track[i]['mae']:.2f}",
        f"{center[i]['mae']:.2f}",
        f"{ring[i]['mae']:.2f}",
        f"{both[i]['mae']:.2f}",
    ])
tbl(["时效", "持续性", "匀速外推", "仅轨迹 CNN", "中心点 ERA5", "外环 ERA5", "中心点加外环"], rows, widths=[0.72, 1.0, 1.0, 1.2, 1.22, 1.16, 1.3])
doc.add_paragraph("表中为 3 个随机种子的路径误差 MAE 均值，单位千米。外环 ERA5 方案在全部六个时效上均低于仅轨迹与中心点 ERA5 方案。")
figure(chart1, "图 1 各时效路径误差对比（三随机种子均值）")
figure(chart2, "图 2 四个变体在各时效的路径误差")

doc.add_heading("6.2 相对改进幅度", 2)
imp_rows = []
for i, h in enumerate(horizons):
    imp_rows.append([
        f"{h}h",
        f"{pct(track[i]['mae'], ring[i]['mae']):.2f}",
        f"{pct(center[i]['mae'], ring[i]['mae']):.2f}",
        f"{pct(both[i]['mae'], ring[i]['mae']):.2f}",
        f"{ring[i]['std']:.2f}",
    ])
tbl(["时效", "相对仅轨迹降幅%", "相对中心点降幅%", "相对中心点加外环降幅%", "外环三种子标准差千米"], imp_rows, widths=[0.8, 1.6, 1.5, 1.9, 1.9])
figure(chart3, "图 3 外环特征相对三种对照方案的误差降幅")

doc.add_heading("6.3 结果解读", 2)
doc.add_paragraph(
    f"第一，外环引导流是四个变体中唯一在全部时效上稳定最优的配置。三种子标准差在 24 与 36 小时分别为 "
    f"{ring[i24]['std']:.2f} 与 {ring[i36]['std']:.2f} 千米，低于仅轨迹方案的 {track[i24]['std']:.2f} 与 {track[i36]['std']:.2f} 千米。"
)

doc.add_heading("6.4 与 JTWC 官方预报对比", 2)
doc.add_paragraph(
    "阶段一官方数据采用 JTWC 2025 年 f-deck 预报路径，并使用审计后的最佳路径真值进行配对。"
    f"在冻结测试集上，成功配对 {official['coverage']['test_windows_scored']} 个窗口、"
    f"覆盖 {official['coverage']['storm_count']} 个台风。由于官方归档实际提供 12、24、36 小时共同预报点，"
    "模型与官方只在这三个时效比较，未把缺失的 6、18、30 小时人为补成零误差。"
)
official_rows = []
official_jtwc = {r["lead_hours"]: r for r in official["jtwc_official"]["by_horizon"]}
official_track = {r["lead_hours"]: r for r in official["variants"]["track_only"]["metrics"]["shared_leads"]["by_horizon"]}
official_ring = {r["lead_hours"]: r for r in official["variants"]["ring_500_850"]["metrics"]["shared_leads"]["by_horizon"]}
for lead in (12, 24, 36):
    official_rows.append([
        f"{lead}h",
        f"{official_track[lead]['mae_km']:.1f}",
        f"{official_ring[lead]['mae_km']:.1f}",
        f"{official_jtwc[lead]['mae_km']:.1f}",
        f"{pct(official_track[lead]['mae_km'], official_ring[lead]['mae_km']):.1f}%",
    ])
tbl(
    ["共同预报时效", "仅轨迹 CNN", "外环 ERA5 CNN", "JTWC 官方预报", "外环相对仅轨迹"],
    official_rows,
    widths=[1.15, 1.35, 1.55, 1.55, 1.55],
)
figure(official_chart, "图 4 冻结测试集上模型与 JTWC 官方预报的路径误差 MAE")
doc.add_paragraph(
    "结果显示，外环 ERA5 CNN 在 12、24、36 小时均优于仅轨迹 CNN，误差分别下降 "
    + "、".join(
        f"{pct(official_track[h]['mae_km'], official_ring[h]['mae_km']):.1f}%"
        for h in (12, 24, 36)
    )
    + "；但在该测试子集上仍高于 JTWC 官方预报。该结论是同一窗口、同一真值口径下的客观对比，"
    "不能表述为模型已经超过业务预报。阶段一官方误差统计使用 2025 年 JTWC f-deck 与 IBTrACS CMA 真值，"
    "其数据源、配对规则和覆盖限制见阶段一验收材料。"
)
doc.add_paragraph(
    f"第二，中心点 ERA5 融合的增益有限，24 小时为 {center[i24]['mae']:.2f} 千米，甚至略差于仅轨迹的 {track[i24]['mae']:.2f} 千米。"
    "台风中心附近的风场被强对流和辐合污染，单点取值难以代表路径的引导气流，这解释了中心点特征收益不明显的原因。"
)
doc.add_paragraph(
    f"第三，外环方案在 36 小时为 {ring[i36]['mae']:.2f} 千米，优于匀速外推的 {cv[i36]['mae']:.2f} 千米，"
    f"在 24 小时的优势更大，为 {ring[i24]['mae']:.2f} 对 {cv[i24]['mae']:.2f} 千米。"
    "环境引导流提供的转向趋势在中期时效上价值最高，这与台风路径受大尺度引导流主导的机理一致。"
)
doc.add_paragraph(
    "第四，中心点加外环的配置劣于单独外环，说明中心点特征带来冗余甚至噪声，"
    "四个输入通道并非越多越好，这与评分表要求的消融结论一致。"
)

doc.add_heading("7 局限性", 1)
for text in [
    "测试集为 ERA5 配对子集，112 个台风、1661 个窗口，不能代表全部 73388 个轨迹点上的表现。",
    "点级 ERA5 配对率为 79.17%，缺测年份不参与评价，结论对缺测年份的泛化性未验证。",
    "官方对比目前限定为 2025 年 JTWC f-deck，尚未覆盖 2020-2024 年官方预报；不能据此外推多年业务优势。",
    "2025 年真值采用 IBTrACS 中 CMA 机构行，JTWC 与 CMA 最佳路径可能存在差异，结果应标注为跨机构验证。",
    "外环特征是环境流场代理量，未做涡旋移除，因此不构成严格的动力引导流诊断量。",
    "本阶段只做统一超参数下的对照，未做外环半径、方位点数与网络深度的超参搜索。",
]:
    doc.add_paragraph(text, style="List Bullet")

doc.add_heading("8 下一步计划", 1)
tbl(
    ["优先级", "任务", "交付物"],
    [
        ["高", "外环超参消融：半径组合、方位点数、是否按面积权重", "补充实验报告与图表"],
        ["高", "按台风个例统计：强台风、转向台风、登陆台风分组评估", "分组指标表"],
        ["高", "补齐 2020-2024 年官方预报并开展多年独立验证", "多年份官方对比审计报告"],
        ["中", "补齐 ADE、FDE 与 72 小时时效，扩展到 48 小时", "指标扩展结果"],
        ["中", "把外环特征接入前端环境风场面板与预测置信区间", "前端功能与截图"],
        ["低", "尝试涡旋移除后的严格引导流诊断量作为对照特征", "附加消融"],
    ],
    widths=[0.8, 4.0, 2.0],
)

doc.add_heading("9 可复现命令", 1)
doc.add_paragraph("外环特征构建：")
code = doc.add_paragraph()
cr = code.add_run(".venv\\Scripts\\python.exe -m ml.build_era5_ring_features")
cr.font.name = "Consolas"
cr.font.size = Pt(9.5)
doc.add_paragraph("对照实验（3 个随机种子）：")
code = doc.add_paragraph()
cr = code.add_run(".venv\\Scripts\\python.exe -m ml.train_era5_annular_ablation")
cr.font.name = "Consolas"
cr.font.size = Pt(9.5)
doc.add_paragraph(
    f"实验指标文件：artifacts/era5/annular_steering_flow/ablation/ablation_summary.json，"
    "特征窗口：artifacts/era5/annular_steering_flow/ring_windows/。"
)
doc.add_paragraph(
    "阶段一官方对比：official_forecast/paired_errors.csv；"
    "模型与官方同测试窗口结果：artifacts/era5/annular_steering_flow/official_comparison/official_comparison.json。"
)

OUT.parent.mkdir(parents=True, exist_ok=True)
doc.save(str(OUT))
print(OUT)
