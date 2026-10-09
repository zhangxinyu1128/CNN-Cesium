# -*- coding: utf-8 -*-
"""Build the stage-4 conformal location-region report as a polished DOCX."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

import stage4_charts
import stage4_data

OUT = Path(r"C:\Users\Lenovo\Desktop\新建文件夹 (2)\CNN-Cesium_阶段四_二维方向性概率锥体实验报告_20261008.docx")
ACCENT = "176B87"
DARK = "102A43"
WARN = "B54708"

data = stage4_data.load()
track, ring = data["track"], data["ring"]
horizons = stage4_data.horizons(track, ring)
charts = stage4_charts.render_all(track, ring, horizons)

t_eval = {h["lead_hours"]: h for h in track["evaluation"]["by_horizon"]}
r_eval = {h["lead_hours"]: h for h in ring["evaluation"]["by_horizon"]}
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
header.text = "CNN-Cesium 阶段四实验报告"
header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
header.runs[0].font.size = Pt(8)
header.runs[0].font.color.rgb = RGBColor.from_string("829AB1")
footer = sec.footer.paragraphs[0]
footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
footer.add_run("阶段四 二维方向性概率锥体实验  |  第 ")
rr = footer.add_run()
a = OxmlElement("w:fldChar"); a.set(qn("w:fldCharType"), "begin")
b = OxmlElement("w:instrText"); b.set(qn("xml:space"), "preserve"); b.text = " PAGE "
c = OxmlElement("w:fldChar"); c.set(qn("w:fldCharType"), "end")
rr._r.extend([a, b, c])
footer.add_run(" 页")

doc.add_paragraph("CNN-Cesium 阶段四 二维方向性概率锥体实验报告", style="Title")
sub = doc.add_paragraph()
rs = sub.add_run("按台风分组共形校准的二维方向性位置不确定区域，并接入主页面预测与风险面板")
rs.font.size = Pt(11)
rs.font.color.rgb = RGBColor.from_string(ACCENT)

doc.add_heading("1 结论摘要", 1)
doc.add_paragraph(
    "本阶段把阶段一二阶段的标量半径不确定性升级为二维方向性椭圆区域。校准在预测中心周围建立局地东/北坐标系，"
    "对残差协方差做 Mahalanobis 分解，以每场台风的最大误差作为共形分数，按台风分组取有限样本分位数，"
    "因此校准粒度是整场台风而不是单个轨迹点。"
)
doc.add_paragraph(
    f"在线轨迹 CNN 在冻结测试集（{track['evaluation']['storm_count']} 个台风、{track['evaluation']['window_count']} 个窗口）上，"
    f"24 小时 90% 椭圆长半轴 {t_eval[24]['ellipse_90']['semi_major_axis_km']:.0f} 千米、短半轴 {t_eval[24]['ellipse_90']['semi_minor_axis_km']:.0f} 千米，"
    f"整场台风覆盖率 {t_eval[24]['ellipse_90']['location_storm_coverage_90']:.2%}；36 小时长半轴 {t_eval[36]['ellipse_90']['semi_major_axis_km']:.0f} 千米、"
    f"短半轴 {t_eval[36]['ellipse_90']['semi_minor_axis_km']:.0f} 千米，覆盖率 {t_eval[36]['ellipse_90']['location_storm_coverage_90']:.2%}。"
)
doc.add_paragraph(
    f"阶段三 ERA5 外环引导流模型的椭圆更紧凑：24 小时长半轴 {r_eval[24]['ellipse_90']['semi_major_axis_km']:.0f} 千米、"
    f"短半轴 {r_eval[24]['ellipse_90']['semi_minor_axis_km']:.0f} 千米，覆盖率 {r_eval[24]['ellipse_90']['location_storm_coverage_90']:.2%}；"
    f"36 小时为 {r_eval[36]['ellipse_90']['semi_major_axis_km']:.0f} 与 {r_eval[36]['ellipse_90']['semi_minor_axis_km']:.0f} 千米。"
    "两套校准结果与各自 checkpoint 一一对应，未混用。"
)
doc.add_paragraph(
    "结论范围：本阶段给出的是基于历史台风分组校准的位置区域，不是实时预报保证，也不是台风灾害发生概率区。"
    "官方业务预报数据仍未获取，本阶段不做与官方预报的对比。"
)

doc.add_heading("2 阶段目标与选题依据", 1)
doc.add_paragraph(
    "内部评审文档把「预测不确定性和概率锥体尚未完成」列为核心扣分项。前一阶段的输出是各时效一个标量半径，"
    "只能画圆，隐含假设是误差各向同性。但台风路径误差有明显的方向性，且随预报时效旋转，"
    "圆形区域既不贴合真实误差形态，也会高估横向范围、低估主方向范围。"
)
doc.add_paragraph(
    "本阶段的目标是把不确定区域从标量半径升级为有方向的二维椭圆，做到三点："
    "一是保留共形校准的有限样本覆盖率保证；二是让区域形状由数据决定而非假设；"
    "三是把结果接入在线预测接口与前端地图，使不确定性与阶段三的精度结论在同一系统内可用。"
)

doc.add_heading("3 校准方法", 1)
doc.add_heading("3.1 局地坐标与协方差", 2)
doc.add_paragraph(
    "对每个预测窗口，把真实位置与预测位置的差投影到以预测中心为原点的局地东/北平面，"
    "经度差按预测中心纬度做余弦订正，得到以千米为单位的东向与北向残差。对这些残差直接估计 2x2 协方差矩阵。"
)
doc.add_heading("3.2 按台风分组的共形分数", 2)
doc.add_paragraph(
    "用 Mahalanobis 距离把每个窗口的二维残差压缩成一个标量分数，再对同一场台风的所有窗口取最大值，"
    "作为该场台风的风险代表。校准分位数在台风级别上取，用有限样本修正 rank = ceil((n+1) * 0.9)。"
    "这样得到的 90% 区域保证的是「整场台风全部窗口都落进去」，而不是「90% 的点落进去」，"
    "比逐点校准严格得多，也更符合业务上对一场台风整体路径的判断。"
)
doc.add_heading("3.3 椭圆参数化", 2)
doc.add_paragraph(
    "对协方差做特征分解，主方向为最大特征值对应的特征向量，椭圆长半轴 = 共形分位数 * sqrt(最大特征值)，"
    "短半轴同理。为了避免样本协方差退化时半轴塌缩，实现中不额外做收缩，而是按各时效的半轴做非退化约束。"
    "方位角按东为 0、北为 90 的顺时针约定给出，长短半轴之比即方向性强度。"
)
tbl(
    ["步骤", "做法"],
    [
        ["残差投影", "经度差按中心纬度余弦订正，东/北分量单位千米"],
        ["分数压缩", "Mahalanobis 距离 sqrt(e' Sigma^-1 e)"],
        ["分组聚合", "同一台风所有窗口取分数最大值"],
        ["分位数", "台风级 rank = ceil((n+1) x 0.9)"],
        ["椭圆还原", "特征分解得长短半轴与方位角，面积 = pi x a x b"],
        ["评价指标", "窗口级与台风级覆盖率、Energy Score、中位误差"],
    ],
    widths=[1.6, 4.9],
)

doc.add_heading("4 数据、划分与防泄漏规则", 1)
doc.add_paragraph(
    "校准集与测试集沿用项目冻结划分，以台风编号或年份为单位切分，不对相邻轨迹点随机切分。"
    "在线轨迹 CNN 用 validation 分区做校准、frozen_test 分区做评价；阶段三外环模型沿用其 ERA5 配对子集划分。"
    "校准文件的 checkpoint SHA256 必须与在线加载的权重一致，否则后端拒绝复用该校准。"
)
tbl(
    ["模型", "校准分区", "校准台风数", "校准窗口数", "评价分区", "评价台风数", "评价窗口数"],
    [
        ["在线轨迹 CNN（track-cnn-1d-v1）", track["calibration"]["split"], str(track["calibration"]["storm_count"]),
         str(track["calibration"]["window_count"]), track["evaluation"]["split"],
         str(track["evaluation"]["storm_count"]), str(track["evaluation"]["window_count"])],
        ["阶段三 ERA5 外环模型", "validation", str(ring["calibration"]["validation_storms"]),
         str(ring["calibration"]["validation_windows"]), "paired test", str(ring["evaluation"]["sample_count"]),
         str(ring["evaluation"]["sample_count"])],
    ],
    widths=[2.0, 1.0, 0.8, 0.8, 0.9, 0.8, 0.8],
)
doc.add_paragraph(
    "Energy Score 是二维位置集合的严格适当评分规则，用于比较不同集合的预报质量，数值单位为千米，"
    "越低越好。它与一维 CRPS 在概念上不同，本报告不把 Energy Score 称为 CRPS。"
)

doc.add_heading("5 实验结果", 1)
doc.add_heading("5.1 椭圆几何参数", 2)
rows = []
for h in horizons:
    t = t_eval[h]["ellipse_90"]
    r = r_eval[h]["ellipse_90"]
    rows.append([
        f"{h}h",
        f"{t['semi_major_axis_km']:.0f}",
        f"{t['semi_minor_axis_km']:.0f}",
        f"{t['bearing_deg']:.1f}",
        f"{t['area_km2'] / 1e6:.2f}",
        f"{r['semi_major_axis_km']:.0f}",
        f"{r['semi_minor_axis_km']:.0f}",
        f"{r['bearing_deg']:.1f}",
    ])
tbl(["时效", "轨迹长半轴", "轨迹短半轴", "轨迹方位角", "轨迹面积百万 km2", "外环长半轴", "外环短半轴", "外环方位角"], rows,
    widths=[0.7, 0.95, 0.95, 0.95, 1.3, 0.95, 0.95, 0.95])
doc.add_paragraph(
    "单位：半轴与方位角按千米与度，面积为百万平方千米。两套模型的方位角都在 56 至 90 度之间，"
    "说明误差主方向偏向东北方向，与台风向西北或西行转向的实际形态一致。"
)
figure(charts[4], "图 1 两套模型的 90% 椭圆长短半轴对比")
figure(charts[0], "图 2 90% 校准椭圆长短半轴随时效变化")
figure(charts[3], "图 3 90% 校准椭圆面积随时效变化")

doc.add_heading("5.2 实际覆盖率与校准质量", 2)
cov_rows = []
for h in horizons:
    t = t_eval[h]["ellipse_90"]
    r = r_eval[h]["ellipse_90"]
    cov_rows.append([
        f"{h}h",
        f"{t['location_coverage_90']:.2%}",
        f"{t['location_storm_coverage_90']:.2%}",
        f"{r['location_coverage_90']:.2%}",
        f"{r['location_storm_coverage_90']:.2%}",
    ])
tbl(["时效", "轨迹窗口级", "轨迹台风级", "外环窗口级", "外环台风级"], cov_rows,
    widths=[0.8, 1.3, 1.3, 1.3, 1.3])
doc.add_paragraph(
    "窗口级覆盖率在 98% 上下，说明 90% 目标略有保守，这是台风级校准的正常代价："
    "为了保证整场台风都落入，区域必然偏保守。台风级覆盖率在 87% 至 97% 之间，"
    "即约每 10 至 13 场台风中会有 1 场至少有一个窗口落到区域外，这正是业务上需要额外关注的量级。"
)
figure(charts[1], "图 4 90% 椭圆在冻结测试集上的实际覆盖率")
figure(charts[5], "图 5 整场台风覆盖率（90% 目标）")

doc.add_heading("5.3 位置集合评分与误差量级", 2)
score_rows = []
for h in horizons:
    t = t_eval[h]
    r = r_eval[h]
    score_rows.append([
        f"{h}h",
        f"{t['energy_score_km']:.1f}",
        f"{t['median_error_km']:.1f}",
        f"{r['energy_score_km']:.1f}",
        f"{r['median_prediction_wind_mae_ms']:.2f}",
    ])
tbl(["时效", "轨迹 Energy Score", "轨迹中位误差 km", "外环 Energy Score", "外环风速 MAE m/s"], score_rows,
    widths=[0.8, 1.6, 1.4, 1.5, 1.5])
doc.add_paragraph(
    f"Energy Score 随时效单调上升，轨迹 CNN 从 {t_eval[6]['energy_score_km']:.1f} 千米升到 {t_eval[36]['energy_score_km']:.1f} 千米，"
    f"外环模型从 {r_eval[6]['energy_score_km']:.1f} 千米升到 {r_eval[36]['energy_score_km']:.1f} 千米，"
    "说明预报时效越长，集合分布与真实位置的分歧越大，这符合集合预报的基本性质。"
)
figure(charts[2], "图 6 位置集合 Energy Score 随时效变化")

doc.add_heading("6 系统接入与验收", 1)
doc.add_paragraph(
    "本阶段的校准结果不是停留在离线文件，而是接入了在线预测链路：后端启动时加载校准文件，"
    "校验 checkpoint SHA256 一致后按预报时效取出椭圆参数，在预测响应的每个时效点上附带区域几何；"
    "响应顶层附带区域类型与风险说明。前端主页面在 Cesium 上按长短半轴与方位角绘制椭圆，"
    "风险面板显示半轴、方位角、面积、覆盖率与 Energy Score。"
)
tbl(
    ["环节", "文件", "状态"],
    [
        ["校准算法", "ml/uncertainty_regions.py", "椭圆校准、覆盖率、Energy Score"],
        ["在线轨迹 CNN 校准", "ml/evaluate_track_api_uncertainty.py", "artifacts/reports/track_api_uncertainty_20261008.json"],
        ["阶段三外环模型校准", "ml/evaluate_era5_uncertainty.py", "artifacts/era5/annular_steering_flow/uncertainty/ring_500_850_uncertainty.json"],
        ["后端预测服务", "backend/app/services/model.py", "按 checkpoint 校验并注入区域参数"],
        ["响应模型", "backend/app/schemas.py", "PredictionRegion 与 uncertainty 区域字段"],
        ["前端地图与面板", "CNN-Cesium/src/App.vue", "绘制椭圆并显示风险指标"],
    ],
    widths=[1.5, 3.1, 2.0],
)
doc.add_paragraph(
    "验收结果：后端单元与集成测试 11 项全部通过；前端 vue-tsc 类型检查零错误、ESLint 零错误、生产构建成功；"
    "实际 POST /api/predict 返回 uncertainty.region_geometry 为 conformal_ellipse 并携带 region_note；"
    "主页面与后端健康检查均返回 HTTP 200。"
)

doc.add_heading("7 局限性", 1)
for text in [
    "本阶段输出的是基于历史台风分组校准的位置区域，不是实时预报保证，也不是灾害发生概率区，前端已明确标注。",
    "台风级覆盖率在 87% 至 97% 之间，仍有约 1 成台风会至少有一个窗口落到区域外，属于保守校准的已知代价。",
    "协方差在 2x2 局地平面估计，未做涡旋移除，也未引入地形或登陆后的路径折转先验。",
    "在线轨迹 CNN 与阶段三外环模型的校准来自不同 checkpoint 与不同划分，两套结果不可直接互相替代，也不应拼接使用。",
    "官方业务预报数据缺失，本阶段不与 CMA 或 NCEP 官方预报对比，也不评价业务可用性。",
    "Energy Score 是二维位置集合评分，与一维 CRPS 在概念上不同，不应混称。",
]:
    doc.add_paragraph(text, style="List Bullet")

doc.add_heading("8 下一步计划", 1)
tbl(
    ["优先级", "任务", "交付物"],
    [
        ["高", "按台风个例统计：强台风、转向台风、登陆台风分组评估椭圆覆盖与误差", "分组指标表与图表"],
        ["高", "接入官方预报数据，做同台风配对的位置误差与区域命中对比", "官方对比审计报告"],
        ["中", "椭圆随强度与转向类型的条件化校准，例如按最大风速分组做分层校准", "分层校准实验"],
        ["中", "把强度不确定性与位置椭圆联动，输出联合的风险区域", "联合预测界面"],
        ["中", "把椭圆区域接入前端导出功能，风险研判面板可一键导出 GeoJSON", "导出功能与验证"],
        ["低", "尝试涡旋移除后的严格引导流诊断量作为对照特征", "附加消融"],
    ],
    widths=[0.8, 4.0, 2.0],
)

doc.add_heading("9 可复现命令", 1)
doc.add_paragraph("在线轨迹 CNN 的二维校准：")
code = doc.add_paragraph()
cr = code.add_run(".venv\\Scripts\\python.exe -m ml.evaluate_track_api_uncertainty")
cr.font.name = "Consolas"
cr.font.size = Pt(9.5)
doc.add_paragraph("阶段三 ERA5 外环模型的二维校准：")
code = doc.add_paragraph()
cr = code.add_run(".venv\\Scripts\\python.exe -m ml.evaluate_era5_uncertainty")
cr.font.name = "Consolas"
cr.font.size = Pt(9.5)
doc.add_paragraph("后端与前端验收：")
code = doc.add_paragraph()
cr = code.add_run(".venv\\Scripts\\python.exe -m unittest backend.tests.test_prediction ml.tests.test_uncertainty")
cr.font.name = "Consolas"
cr.font.size = Pt(9.5)
doc.add_paragraph(
    f"在线校准结果：artifacts/reports/track_api_uncertainty_20261008.json，"
    "外环模型校准结果：artifacts/era5/annular_steering_flow/uncertainty/ring_500_850_uncertainty.json。"
)

OUT.parent.mkdir(parents=True, exist_ok=True)
doc.save(str(OUT))
print(OUT)
