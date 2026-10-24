# AI 研判助手 · 使用与部署说明

## 1. 功能简介
- 大屏主模式右上角「✦ AI 研判」悬浮聊天面板。
- 离线可用：内置知识卡片（cards.json）+ 规则检索，零外部依赖。
- 可选智能模式：配置 DeepSeek API Key 后自动升级为自然语言问答，无 key 或调用失败自动降级回离线规则。
- 每条回答附带证据来源（sources，指向报告/代码文件路径）。
- 可读取当前选中台风、预测时效、预测位置、ERA5 风场、误差范围等实时上下文。
- 明确区分「在线纯轨迹 1D CNN」与「ERA5 历史融合实验」。

## 2. 相关文件
| 类型 | 路径 | 作用 |
|---|---|---|
| 知识卡片 | backend/app/knowledge/cards.json | 固定问答语料（改这里无需改代码） |
| 规则引擎 | backend/app/services/knowledge.py | 卡片检索 + 实时上下文组装 |
| LLM 客户端 | backend/app/services/llm.py | DeepSeek 调用（零新增依赖） |
| 后端入口 | backend/app/main.py | /api/assistant/query 端点 |
| 前端面板 | CNN-Cesium/src/components/AssistantPanel.vue | 悬浮聊天 UI |
| 测试 | backend/tests/test_assistant.py, test_llm.py | 自动化测试 |

## 3. 环境要求
- Python 3.8+（仅 AI 助手部分即可；若要跑 CNN 预测 /api/predict，需 Python 3.11 + torch，见「常见问题」）
- 后端依赖：fastapi / uvicorn / pydantic（AI 助手无需 torch）
- 前端：Node.js 20+，pnpm 或 npm

## 4. 部署步骤

### 4.1 后端
```powershell
cd e:\比赛\CNN-Cesium
# 首次：py -3.11 -m venv .venv   # 若已有 .venv 可跳过
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt   # 仅 AI 助手部分所需
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

### 4.2 前端
```powershell
cd e:\比赛\CNN-Cesium\CNN-Cesium
corepack pnpm dev     # 或 npm run dev
```
访问 http://127.0.0.1:10060/ ，点右上角「✦ AI 研判」。

## 5. 配置 DeepSeek（可选，默认离线）
# 在启动后端之前设置（同终端）：
```powershell
$env:DEEPSEEK_API_KEY = "sk-你的key"
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```
- 有 key：自动走 DeepSeek 智能问答。
- 无 key / 设为 $null / key 失效：自动降级为离线规则问答。
- 可选环境变量：DEEPSEEK_BASE_URL（默认 https://api.deepseek.com）、DEEPSEEK_MODEL（默认 deepseek-chat，演示求快别用 reasoner）、DEEPSEEK_TIMEOUT（默认 10）。

## 6. 使用 / 测试问题清单
固定问题：在线预测模型是什么 / ERA5 融合有效吗 / 与官方预报差多少 / 误差范围口径 / 数据范围
动态问题（先选中台风并点「运行 CNN 预测」）：当前台风是什么 / 当前预测位置在哪 / 当前误差范围是多少 / 当前环境风场怎么样
区分验证：在线预测和 ERA5 融合实验有什么区别
兜底验证：今天天气怎么样（应回「知识库中暂未收录该信息」）

## 7. 如何新增 / 修改知识卡片
编辑 backend/app/knowledge/cards.json，每张卡结构：
{ "id": "唯一英文", "title": "短标题", "keywords": ["强关联词..."], "weight": 1,
  "answer": "2-4 句确定结论（带数字）", "sources": [{"path": "相对路径", "section": "小节"}], "caveats": ["口径红线"] }
注意：
- keywords 用强关联词，禁用宽泛单字（如「模型」）；实时值问题不要写成卡片（走 context 自动读取）。
- 改完必须重启后端（卡片在启动时加载）。

## 8. 常见问题
- pnpm 找不到：用 corepack pnpm dev，或 npm run dev。
- PowerShell 测试 curl 中文乱码：用 Invoke-RestMethod + UTF8 字节数组，或先 [Console]::OutputEncoding = [System.Text.Encoding]::UTF8。
- 测试 test_prediction 报 No module named 'torch'：AI 助手不依赖 torch，此为 CNN 模型测试；若要跑需 Python 3.11 环境。
- 问口语化问题回「未收录」：卡片缺同义词，去 cards.json 补关键词。

## 9. 安全提示
- API Key 不要贴进聊天、文档、Git 仓库；只放在本机环境变量。
- Key 一旦泄露（出现在聊天/截图/日志），立即到 platform.deepseek.com 吊销并重新生成。
