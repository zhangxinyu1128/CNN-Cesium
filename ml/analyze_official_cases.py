"""Stratify the JTWC comparison by turning and rapid-motion cases.

The analysis deliberately uses the already frozen, paired official-comparison
output. It does not retrain a model or change the test split. Turning is
classified from the last two observed six-hour track segments. Rapid motion is
defined as the empirical top quartile of the recent two-segment translation
speed among the scored windows, so the threshold is recorded with the result.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from ml.data_audit import PROJECT_ROOT


MODEL_LEADS = (6, 12, 18, 24, 30, 36)
SHARED_LEADS = (12, 24, 36)
MODEL_INDEX = {lead: index for index, lead in enumerate(MODEL_LEADS)}
# evaluate_official_forecast serializes the model's supported official leads as
# (6, 12, 24, 36, 48, 72); the 6-hour slot is normally empty in the archive.
OFFICIAL_INDEX = {lead: index for index, lead in enumerate((6, 12, 24, 36, 48, 72))}
TURN_GROUPS = ("turning_ge_30deg", "moderate_15_30deg", "steady_le_15deg", "weak_motion")
MOTION_GROUPS = ("rapid_top_quartile", "normal_motion")
JOINT_GROUPS = ("turning_and_rapid", "turning_only", "rapid_only", "neither")


def wall_clock_key(value: str) -> str:
    """Return a cycle key without changing the source's displayed clock.

    The project track timestamps and the serialized comparison use the same
    six-hour cycle, although the latter may carry a timezone suffix. Matching
    wall-clock fields avoids silently shifting a cycle by eight hours.
    """

    return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y%m%d%H")


def haversine_km(first: Sequence[float], second: Sequence[float]) -> float:
    """Great-circle distance between two ``(longitude, latitude)`` points."""

    lon1, lat1 = map(float, first[:2])
    lon2, lat2 = map(float, second[:2])
    radians = math.pi / 180.0
    dlat = (lat2 - lat1) * radians
    dlon = ((lon2 - lon1 + 180.0) % 360.0 - 180.0) * radians
    phi1, phi2 = lat1 * radians, lat2 * radians
    value = math.sin(dlat / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlon / 2.0) ** 2
    return 6371.0088 * 2.0 * math.atan2(math.sqrt(value), math.sqrt(max(1.0 - value, 0.0)))


def bearing_deg(first: Sequence[float], second: Sequence[float]) -> float:
    """Initial bearing from the first point to the second point."""

    lon1, lat1 = map(math.radians, (float(first[0]), float(first[1])))
    lon2, lat2 = map(math.radians, (float(second[0]), float(second[1])))
    dlon = (lon2 - lon1 + math.pi) % (2.0 * math.pi) - math.pi
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(y, x)) % 360.0


def classify_observed_window(
    row: Mapping[str, Any],
    normalizer: Mapping[str, Sequence[float]],
    rapid_threshold_kmh: Optional[float] = None,
) -> Dict[str, Any]:
    """Classify a test window from its observed history."""

    mean = np.asarray(normalizer["mean"][:6], dtype=np.float64)
    scale = np.asarray(normalizer["std"][:6], dtype=np.float64)
    raw = np.asarray(row["x"], dtype=np.float64) * scale + mean
    positions = raw[:, :2]
    distances = [haversine_km(positions[index - 1], positions[index]) for index in (2, 3)]
    bearings = [bearing_deg(positions[index - 1], positions[index]) for index in (2, 3)]
    turn_angle = abs((bearings[1] - bearings[0] + 180.0) % 360.0 - 180.0)
    recent_speed_kmh = float(np.mean(distances) / 6.0)
    if min(distances) < 50.0:
        turn_group = "weak_motion"
    elif turn_angle >= 30.0:
        turn_group = "turning_ge_30deg"
    elif turn_angle <= 15.0:
        turn_group = "steady_le_15deg"
    else:
        turn_group = "moderate_15_30deg"
    rapid = rapid_threshold_kmh is not None and recent_speed_kmh >= rapid_threshold_kmh
    turning = turn_group == "turning_ge_30deg"
    if turning and rapid:
        joint_group = "turning_and_rapid"
    elif turning:
        joint_group = "turning_only"
    elif rapid:
        joint_group = "rapid_only"
    else:
        joint_group = "neither"
    return {
        "turn_group": turn_group,
        "motion_group": "rapid_top_quartile" if rapid else "normal_motion",
        "joint_group": joint_group,
        "turn_angle_deg": float(turn_angle),
        "recent_speed_kmh": recent_speed_kmh,
        "segment_distances_km": [float(value) for value in distances],
    }


def _finite_position(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) >= 2 and all(math.isfinite(float(item)) for item in value[:2])


def _error(prediction: Any, truth: Any) -> Optional[float]:
    if not _finite_position(prediction) or not _finite_position(truth):
        return None
    return haversine_km(prediction, truth)


def _summary(values: Sequence[float], storm_ids: Sequence[str]) -> Dict[str, Any]:
    data = np.asarray(values, dtype=np.float64)
    if data.size == 0:
        return {
            "sample_count": 0,
            "storm_count": 0,
            "mae_km": None,
            "rmse_km": None,
            "median_km": None,
            "p90_km": None,
            "storm_mean_mae_km": None,
        }
    by_storm: Dict[str, List[float]] = defaultdict(list)
    for storm, value in zip(storm_ids, data):
        by_storm[str(storm)].append(float(value))
    storm_means = [float(np.mean(item)) for item in by_storm.values()]
    return {
        "sample_count": int(data.size),
        "storm_count": len(by_storm),
        "mae_km": float(np.mean(data)),
        "rmse_km": float(np.sqrt(np.mean(data**2))),
        "median_km": float(np.median(data)),
        "p90_km": float(np.percentile(data, 90)),
        "storm_mean_mae_km": float(np.mean(storm_means)),
    }


def _group_metrics(records: Sequence[Mapping[str, Any]], group_key: str, groups: Iterable[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for group in groups:
        selected = [item for item in records if item[group_key] == group]
        by_horizon = []
        for lead in SHARED_LEADS:
            model_values: List[float] = []
            model_storms: List[str] = []
            official_values: List[float] = []
            official_storms: List[str] = []
            model_index = MODEL_INDEX[lead]
            official_index = OFFICIAL_INDEX[lead]
            for item in selected:
                truth = item["truth_lat_lon"][model_index]
                model_error = _error(item["model_lat_lon"][model_index], truth)
                if model_error is not None:
                    model_values.append(model_error)
                    model_storms.append(item["storm"])
                official_error = _error(item["jtwc_lat_lon"][official_index], truth)
                if official_error is not None:
                    official_values.append(official_error)
                    official_storms.append(item["storm"])
            by_horizon.append({
                "lead_hours": lead,
                "model": _summary(model_values, model_storms),
                "jtwc_official": _summary(official_values, official_storms),
            })
        result[group] = {
            "window_count": len(selected),
            "storm_count": len({item["storm"] for item in selected}),
            "storms": sorted({item["storm"] for item in selected}),
            "by_horizon": by_horizon,
        }
    return result


def _worst_cases(records: Sequence[Mapping[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
    scored = []
    for item in records:
        index = MODEL_INDEX[36]
        truth = item["truth_lat_lon"][index]
        model_error = _error(item["model_lat_lon"][index], truth)
        if model_error is None:
            continue
        official_error = _error(item["jtwc_lat_lon"][OFFICIAL_INDEX[36]], truth)
        scored.append({
            "typhoon_id": item["typhoon_id"],
            "storm": item["storm"],
            "issue_cycle": item["issue_cycle"],
            "turn_group": item["turn_group"],
            "motion_group": item["motion_group"],
            "turn_angle_deg": round(item["turn_angle_deg"], 1),
            "recent_speed_kmh": round(item["recent_speed_kmh"], 1),
            "model_36h_error_km": round(model_error, 1),
            "jtwc_36h_error_km": None if official_error is None else round(official_error, 1),
        })
    return sorted(scored, key=lambda item: item["model_36h_error_km"], reverse=True)[:limit]


def _fmt(value: Optional[float]) -> str:
    return "--" if value is None else f"{value:.1f}"


def _markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "# 官方预报场景分组分析报告",
        "",
        "## 1 结论",
        "",
        f"本轮使用官方对比的 {result['coverage']['window_count']} 个测试窗口，覆盖 {result['coverage']['storm_count']} 个台风。分析只对 2025 年 JTWC f-deck 与模型共有的 12/24/36 小时进行分组，不重新训练、不改变冻结测试集。",
        "",
        "分组结果用于定位模型在转向和快速移动阶段的失效模式，属于诊断性分析，不能据此推断因果或外推到其他年份。登陆分类暂缓，因为当前项目没有经过审计的海岸线矢量数据。",
        "",
        "## 2 分组定义",
        "",
        "| 分组 | 定义 |",
        "| --- | --- |",
        "| 转向 | 最近两个六小时观测段的方向变化达到 30°；两个观测段均至少 50 km |",
        "| 中等转向 | 方向变化大于 15° 且小于 30° |",
        "| 稳定移动 | 方向变化不超过 15° 且两个观测段均至少 50 km |",
        "| 弱移动 | 最近两个观测段任一距离小于 50 km |",
        f"| 快速移动 | 最近两个观测段平均平移速度不低于本批窗口的第 75 百分位：{result['thresholds']['rapid_speed_kmh']:.1f} km/h |",
        "",
        "## 3 转向分组结果",
        "",
        "| 分组 | 窗口数 | 台风数 | 时效 | JTWC MAE km | ERA5 模型 MAE km |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for group, item in result["dimensions"]["turn_group"].items():
        for metric in item["by_horizon"]:
            lines.append(
                f"| {group} | {item['window_count']} | {item['storm_count']} | {metric['lead_hours']}h | {_fmt(metric['jtwc_official']['mae_km'])} | {_fmt(metric['model']['mae_km'])} |"
            )
    lines += [
        "",
        "## 4 快速移动分组结果",
        "",
        "| 分组 | 窗口数 | 台风数 | 时效 | JTWC MAE km | ERA5 模型 MAE km |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for group, item in result["dimensions"]["motion_group"].items():
        for metric in item["by_horizon"]:
            lines.append(
                f"| {group} | {item['window_count']} | {item['storm_count']} | {metric['lead_hours']}h | {_fmt(metric['jtwc_official']['mae_km'])} | {_fmt(metric['model']['mae_km'])} |"
            )
    lines += [
        "",
        "## 5 失效样本定位",
        "",
        "下表按 ERA5 模型 36 小时位置误差排序，用于后续查看转向和快速移动案例；它不是新的测试集指标。",
        "",
        "| 台风 | 起报周期 | 转向组 | 移动组 | 转向角度° | 近期速度 km/h | 模型 36h km | JTWC 36h km |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for item in result["worst_cases"]:
        lines.append(
            f"| {item['typhoon_id']} | {item['issue_cycle']} | {item['turn_group']} | {item['motion_group']} | {item['turn_angle_deg']:.1f} | {item['recent_speed_kmh']:.1f} | {item['model_36h_error_km']:.1f} | {_fmt(item['jtwc_36h_error_km'])} |"
        )
    lines += [
        "",
        "## 6 证据边界与下一步",
        "",
        "- 窗口之间存在重叠，置信区间和显著性检验不能把窗口当作独立样本；本报告只给描述性统计。",
        "- 登陆阶段需要海岸线矢量和统一的登陆判定规则，当前没有这项数据，因此不输出登陆组指标。",
        "- 快速移动采用本批数据的经验第 75 百分位，不等价于业务部门固定阈值；补充更多年份后应重新审计阈值稳定性。",
        "- 下一项优先工作是把环境场从环带均值扩展为更大空间范围和风切变特征，并保持同一冻结测试集做消融。",
        "",
        "## 7 复现",
        "",
        "```powershell",
        "cd D:\\project\\CNN-Cesium",
        ".venv\\Scripts\\python.exe -m ml.analyze_official_cases",
        "```",
        "",
        "结果 JSON：`artifacts/reports/official_case_analysis_20261008.json`。",
    ]
    return "\n".join(lines) + "\n"


def run(
    comparison_path: Path,
    test_path: Path,
    manifest_path: Path,
    output_json: Path,
    output_markdown: Path,
) -> Dict[str, Any]:
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in test_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    row_by_key = {
        (str(row["typhoon_id"]), wall_clock_key(row["history_times"][-1])): row
        for row in rows
    }
    track_windows = comparison["variants"]["track_only"]["predictions_by_window"]
    ring_windows = comparison["variants"]["ring_500_850"]["predictions_by_window"]
    if len(track_windows) != len(ring_windows):
        raise ValueError("track_only and ring_500_850 window counts differ")

    base_records = []
    missing_rows = []
    for track_item, ring_item in zip(track_windows, ring_windows):
        if track_item["typhoon_id"] != ring_item["typhoon_id"] or track_item["issue_utc"] != ring_item["issue_utc"]:
            raise ValueError("comparison variants are not aligned")
        key = (str(track_item["typhoon_id"]), wall_clock_key(track_item["issue_utc"]))
        row = row_by_key.get(key)
        if row is None:
            missing_rows.append({"typhoon_id": key[0], "issue_cycle": key[1]})
            continue
        base_records.append({
            **track_item,
            "model_lat_lon": ring_item["model_lat_lon"],
            "issue_cycle": key[1],
            **classify_observed_window(row, manifest["preprocessing"]["normalizer"]),
        })
    if missing_rows:
        raise ValueError(f"could not match {len(missing_rows)} official windows to frozen test rows")

    speeds = np.asarray([item["recent_speed_kmh"] for item in base_records], dtype=np.float64)
    rapid_threshold = float(np.quantile(speeds, 0.75))
    for item in base_records:
        item.update(classify_observed_window(
            row_by_key[(str(item["typhoon_id"]), item["issue_cycle"])],
            manifest["preprocessing"]["normalizer"],
            rapid_threshold,
        ))

    result: Dict[str, Any] = {
        "experiment": "jtwc_official_case_stratification",
        "source": {
            "comparison": str(comparison_path.resolve()),
            "frozen_test": str(test_path.resolve()),
            "normalizer": str(manifest_path.resolve()),
            "official_agency": comparison["official_source"]["agency"],
            "official_years": comparison["official_source"]["years"],
        },
        "coverage": {
            "window_count": len(base_records),
            "storm_count": len({item["storm"] for item in base_records}),
            "shared_leads_hours": list(SHARED_LEADS),
        },
        "thresholds": {
            "weak_motion_segment_km": 50.0,
            "turning_angle_deg": 30.0,
            "rapid_speed_quantile": 0.75,
            "rapid_speed_kmh": rapid_threshold,
        },
        "dimensions": {
            "turn_group": _group_metrics(base_records, "turn_group", TURN_GROUPS),
            "motion_group": _group_metrics(base_records, "motion_group", MOTION_GROUPS),
            "joint_group": _group_metrics(base_records, "joint_group", JOINT_GROUPS),
        },
        "landfall_analysis": {
            "status": "deferred",
            "reason": "当前项目没有经过审计的海岸线矢量数据和统一登陆判定规则。",
        },
        "worst_cases": _worst_cases(base_records),
        "limitations": [
            "窗口之间重叠，分组结果为描述性统计，不能当作独立样本显著性结论。",
            "快速移动阈值为本批评分窗口的经验第 75 百分位，补充年份后需重新审计。",
            "官方资料仅覆盖 2025 年，且严格可比时效为 12/24/36 小时。",
        ],
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_markdown.write_text(_markdown(result), encoding="utf-8")
    print(json.dumps({"json": str(output_json.resolve()), "markdown": str(output_markdown.resolve()), "coverage": result["coverage"]}, ensure_ascii=False))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path, default=PROJECT_ROOT / "artifacts" / "era5" / "annular_steering_flow" / "official_comparison" / "official_comparison.json")
    parser.add_argument("--test", type=Path, default=PROJECT_ROOT / "data" / "processed" / "test.jsonl")
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "data" / "processed" / "manifest.json")
    parser.add_argument("--output-json", type=Path, default=PROJECT_ROOT / "artifacts" / "reports" / "official_case_analysis_20261008.json")
    parser.add_argument("--output-markdown", type=Path, default=PROJECT_ROOT / "artifacts" / "reports" / "official_case_analysis_20261008.md")
    args = parser.parse_args()
    run(args.comparison, args.test, args.manifest, args.output_json, args.output_markdown)


if __name__ == "__main__":
    main()
