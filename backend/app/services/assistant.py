"""Evidence-grounded assistant for the typhoon analysis workspace.

The local path is deliberately small and deterministic so the feature remains
usable in an offline competition demo. DeepSeek is an optional answer writer;
it receives only the selected evidence cards and a compact page context.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


KNOWLEDGE_CARDS: List[Dict[str, Any]] = [
    {
        "title": "在线接口模型口径",
        "source": "backend/app/services/model.py；/api/predict",
        "keywords": ["在线", "接口", "当前模型", "cnn", "模型版本", "实时", "era5", "风场输入"],
        "excerpt": (
            "当前默认在线 /api/predict 使用 12 步局地运动坐标、多尺度因果残差 CNN 和物理约束训练权重，"
            "请求包含 13 个连续六小时观测点（前置点用于计算运动特征），输出未来 6、12、18、24、30、36 小时位置和风速。"
            "在线接口没有把 ERA5 风场作为模型输入；页面展示的 ERA5 是沿轨环境背景。"
        ),
    },
    {
        "title": "ERA5 历史融合实验",
        "source": "artifacts/era5/historical_500_850_refresh_20261002/ablation_layers",
        "keywords": ["era5", "500", "850", "融合", "实验", "对照", "mae", "误差", "效果"],
        "excerpt": (
            "ERA5 500/850 hPa 融合模型是历史实验版本，用于固定测试集上的单独对照，不能说成当前在线接口已经接入。"
            "三随机种子均值的路径 MAE 约为 6/12/18/24/30/36 小时 41.93、81.18、127.01、179.85、239.52、306.65 km。"
        ),
    },
    {
        "title": "数据划分与防泄露",
        "source": "data/processed/manifest.json；实验 metrics.json",
        "keywords": ["数据", "样本", "划分", "训练", "验证", "测试", "泄露", "年份", "台风"],
        "excerpt": (
            "数据覆盖 1945—2025 年，整理得到 1935 场有效台风。按台风个例和年份冻结子集："
            "训练 1945—2016，验证 2017—2019，测试 2020—2025；相邻时间窗口不会同时进入训练和测试。"
        ),
    },
    {
        "title": "误差范围与共形校准",
        "source": "artifacts/reports/track_api_uncertainty_multiscale_physics_2028.json",
        "keywords": ["不确定度", "误差范围", "90%", "覆盖率", "共形", "半径", "椭圆", "概率", "风险"],
        "excerpt": (
            "当前多尺度物理约束模型的位置半径来自验证集台风分组 split-conformal 校准，目标覆盖率为 90%，不是实时灾害概率。"
            "独立测试的整场台风覆盖率仍有时效低于 90%；圆形半径大致从 6 小时 153 km 增至 36 小时 1,146 km。"
        ),
    },
    {
        "title": "项目功能与使用边界",
        "source": "CNN-Cesium/src；backend/app/main.py",
        "keywords": ["功能", "应用", "地图", "三维", "回放", "导出", "研判", "教学", "科研", "预警"],
        "excerpt": (
            "系统支持 Cesium 三维历史回放、在线轨迹 CNN 预测、沿台风中心匹配的 ERA5 500/850 hPa 风矢量、"
            "误差范围展示和 JSON/GeoJSON 导出。结果用于科研复核、教学演示和辅助研判，不替代官方预警。"
        ),
    },
    {
        "title": "AI+台风研判智能助手",
        "source": "backend/app/services/assistant.py；CNN-Cesium/src/components/TyphoonAssistant.vue",
        "keywords": ["创新", "ai", "助手", "问答", "智能", "知识库", "解释", "引用", "多模态", "大模型"],
        "excerpt": (
            "新增的 AI+ 模块不是普通 FAQ：它把项目模型说明、实验指标、数据边界和限制整理成可检索知识卡片，"
            "再注入当前选中的台风、预测时效、ERA5 风场和校准范围，返回带来源引用的研判解释。默认本地证据回答，"
            "配置 DeepSeek 后只让大模型组织已检索证据，失败时自动回退本地回答。"
        ),
    },
    {
        "title": "技术创新链路",
        "source": "项目方案：轨迹先验 + 环境场实验 + 证据约束问答",
        "keywords": ["技术路线", "创新点", "深度学习", "残差", "先验", "解释性", "可解释"],
        "excerpt": (
            "模型侧以局地东-北位移和匀速移动作为先验，由多尺度因果残差 CNN 学习修正，并以物理损失约束运动；"
            "ERA5 500/850 hPa 融合仍是独立历史实验，不是当前在线输入；"
            "应用侧由研判助手把数值预测、不确定度和环境背景组织成可追问、可引用的解释。三部分分别对应预测、验证和交互解释。"
        ),
    },
]

SUGGESTED_QUESTIONS = [
    "当前在线模型使用了 ERA5 吗？",
    "24 小时预测误差应该怎么解释？",
    "这个项目的 AI+ 创新点是什么？",
    "当前台风的预测结果和误差范围如何看？",
]


def _normalise_question(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip().lower())


def _retrieve(question: str, limit: int = 3) -> List[Dict[str, Any]]:
    normalised = _normalise_question(question)
    scored: List[Tuple[int, int, Dict[str, Any]]] = []
    for index, card in enumerate(KNOWLEDGE_CARDS):
        score = 0
        for keyword in card["keywords"]:
            if keyword.lower() in normalised:
                score += 4 if len(keyword) > 1 else 1
        if card["title"].lower() in normalised:
            score += 3
        scored.append((score, -index, card))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = [item[2] for item in scored if item[0] > 0][:limit]
    if selected:
        return selected
    return [KNOWLEDGE_CARDS[0], KNOWLEDGE_CARDS[4], KNOWLEDGE_CARDS[5]]


def _number(value: Any, digits: int = 1) -> Optional[str]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.{digits}f}"
    return None


def _compact_context(context: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only fields that can help explain the visible state."""
    selected = context.get("selected_typhoon")
    point = context.get("current_point")
    forecast = context.get("forecast")
    era5 = context.get("era5")
    model = context.get("model")
    result: Dict[str, Any] = {}
    if isinstance(selected, dict):
        result["selected_typhoon"] = {
            key: selected.get(key)
            for key in ("tfbh", "name", "ename")
            if selected.get(key) not in (None, "")
        }
    if isinstance(point, dict):
        result["current_point"] = {
            key: point.get(key)
            for key in ("time", "lng", "lat", "speed", "power")
            if point.get(key) is not None
        }
    if isinstance(forecast, dict):
        result["forecast"] = {
            key: forecast.get(key)
            for key in ("lead_hours", "lng", "lat", "speed_ms", "location_radius_90_km")
            if forecast.get(key) is not None
        }
    if isinstance(era5, dict):
        levels = era5.get("levels")
        result["era5"] = {
            "levels": levels if isinstance(levels, dict) else {},
            "status": era5.get("status"),
        }
    if isinstance(model, dict):
        result["model"] = {
            key: model.get(key)
            for key in ("status", "model_type", "model_version", "coordinate_system", "input_steps", "device")
            if model.get(key) is not None
        }
    for key in ("selected_horizon",):
        if context.get(key) is not None:
            result[key] = context[key]
    return result


def _context_sentence(context: Dict[str, Any]) -> str:
    compact = _compact_context(context)
    selected = compact.get("selected_typhoon", {})
    point = compact.get("current_point", {})
    forecast = compact.get("forecast", {})
    era5 = compact.get("era5", {})
    name = selected.get("name") or selected.get("ename") or selected.get("tfbh")
    parts: List[str] = []
    if name:
        parts.append(f"当前选中台风：{name}")
    if point.get("time"):
        parts.append(
            f"观测点 {point.get('time')}，位置 {point.get('lat', '--')}°N/{point.get('lng', '--')}°E"
        )
    if forecast:
        radius = _number(forecast.get("location_radius_90_km"), 0)
        parts.append(
            f"当前选择 {forecast.get('lead_hours', '--')} 小时预测，位置 "
            f"{forecast.get('lat', '--')}°N/{forecast.get('lng', '--')}°E"
            + (f"，历史校准半径约 {radius} km" if radius else "")
        )
    levels = era5.get("levels") if isinstance(era5, dict) else None
    if isinstance(levels, dict) and levels:
        level_text = []
        for level in ("500", "850"):
            wind = levels.get(level)
            if isinstance(wind, dict) and wind.get("speed_ms") is not None:
                level_text.append(f"{level} hPa {wind.get('speed_ms')} m/s")
        if level_text:
            parts.append("当前匹配 ERA5 环境风：" + "、".join(level_text))
    return "；".join(parts) if parts else "当前页面还没有选中台风或预测结果。"


def _local_answer(question: str, context: Dict[str, Any], cards: List[Dict[str, Any]]) -> str:
    normalised = _normalise_question(question)
    compact = _compact_context(context)
    forecast = compact.get("forecast", {})
    lines: List[str] = []
    if any(token in normalised for token in ("era5", "风场输入", "在线模型", "当前模型")):
        lines.append(
            "当前默认在线接口使用局地东-北运动坐标、多尺度因果残差 CNN 和物理约束训练；"
            "需要 13 个连续六小时观测点，包含 72 小时模型历史及一个前置观测。"
        )
        lines.append(
            "ERA5 500/850 hPa 没有作为 /api/predict 的输入；页面中的 ERA5 风矢量用于环境背景和结果解释。"
        )
    elif any(token in normalised for token in ("创新", "ai+", "助手", "问答", "智能")):
        lines.append(
            "AI+ 的核心不是加一个普通聊天框，而是把项目证据和当前台风状态绑定起来。"
        )
        lines.append(
            "助手先检索模型、指标和限制，再结合当前预测、ERA5 风场与历史校准范围生成带来源的解释；"
            "没有外部密钥时仍可离线回答。"
        )
    elif any(token in normalised for token in ("误差", "24小时", "24 小时", "准确", "效果")):
        lines.append(
            "当前默认物理约束多尺度模型在 914 个留出台风窗口上的 24 小时路径 MAE 约为 185.2 km。"
            "对照显示它优于旧基线；与原残差 CNN 的差异未达到统计显著，不能声称已证实更优。"
        )
        lines.append(
            "在线结果应同时查看页面给出的预测位置和 90% 历史校准范围，不能把范围当成灾害概率。"
        )
    elif any(token in normalised for token in ("当前台风", "预测结果", "怎么解释", "位置", "预报")):
        lines.append(_context_sentence(context))
        if forecast:
            radius = _number(forecast.get("location_radius_90_km"), 0)
            if radius:
                lines.append(
                    f"这个半径表示在线模型在历史校准中的位置误差范围参考（约 {radius} km），"
                    "不是当前台风一定会影响的范围，也不是官方预警。"
                )
        else:
            lines.append("请先在地图中运行一次 6—36 小时预测，助手才能解释具体预测点。")
    elif any(token in normalised for token in ("数据", "训练", "测试", "泄露", "样本")):
        lines.append(
            "项目使用 1945—2025 年 1935 场有效台风，并按台风个例和年份固定划分训练、验证和测试集，"
            "避免相邻时间窗口同时出现在训练与测试中。"
        )
    else:
        lines.append(cards[0]["excerpt"])
        lines.append("你可以继续问模型口径、ERA5 实验、误差范围或当前预测，我会附上对应证据来源。")
    return "\n".join(lines)


def _deepseek_answer(question: str, context: Dict[str, Any], cards: List[Dict[str, Any]]) -> Optional[str]:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    provider = os.getenv("TC_ASSISTANT_PROVIDER", "local").strip().lower()
    if provider != "deepseek" or not api_key:
        return None
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    evidence = "\n".join(
        f"[{index}] {card['title']}：{card['excerpt']}（来源：{card['source']}）"
        for index, card in enumerate(cards, start=1)
    )
    system = (
        "你是台风研判智能助手。只根据提供的证据和页面上下文回答，使用简洁中文。"
        "必须区分在线局地运动多尺度物理约束CNN与ERA5历史融合实验，不能编造卫星、多模式或实时预警能力。"
        "回答末尾用[1]这类标记引用证据；如果证据不足，明确说无法从当前项目数据判断。"
    )
    prompt = (
        f"用户问题：{question}\n\n页面上下文：{json.dumps(_compact_context(context), ensure_ascii=False)}\n\n"
        f"证据卡片：\n{evidence}"
    )
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 500,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        answer = payload.get("choices", [{}])[0].get("message", {}).get("content")
        return answer.strip() if isinstance(answer, str) and answer.strip() else None
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, IndexError):
        return None


def answer_question(question: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    safe_question = question.strip()
    safe_context = context if isinstance(context, dict) else {}
    cards = _retrieve(safe_question)
    deepseek_answer = _deepseek_answer(safe_question, safe_context, cards)
    if deepseek_answer:
        answer = deepseek_answer
        mode = "deepseek_grounded"
    else:
        answer = _local_answer(safe_question, safe_context, cards)
        mode = "local_grounded"
    return {
        "answer": answer,
        "mode": mode,
        "citations": [
            {"title": card["title"], "source": card["source"], "excerpt": card["excerpt"]}
            for card in cards
        ],
        "suggested_questions": SUGGESTED_QUESTIONS,
    }
