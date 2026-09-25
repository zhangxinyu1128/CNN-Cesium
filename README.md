# 基于 CNN 与 Cesium 的热带气旋可视化与预测系统

一个面向热带气旋历史轨迹分析、CNN 路径与强度预测以及 Cesium 三维展示的前后端项目。

项目包含前端源码、FastAPI 后端、训练脚本、数据集、模型权重和课程材料。推荐按“先启动后端，再启动前端”的顺序运行。

## 项目能力

- 按年份、编号和名称查询热带气旋历史数据
- Cesium 三维地球展示历史轨迹、当前点和预测轨迹
- CNN 多步预测未来 6、12、18、24、30、36 小时的路径与风速
- ECharts 展示轨迹参数和预测结果
- FastAPI 提供健康检查、历史数据和预测接口
- 提供数据审计、6 小时重采样、数据集构建和模型训练脚本

## 技术栈

| 层次 | 技术 |
| --- | --- |
| 前端 | Vue 3、TypeScript、Vite 6、Cesium、Pinia、Element Plus、ECharts |
| 后端 | Python、FastAPI、Uvicorn |
| 模型 | PyTorch 2.3.1、NumPy 1.24.4、1D CNN 多任务预测 |
| 数据 | 台风年度索引、历史轨迹 JSON、处理后的训练窗口 |

## 目录结构

```text
CNN-Cesium/
├─ CNN-Cesium/                 # Vue + Cesium 前端
│  ├─ src/                     # 页面、组件、服务和类型
│  ├─ public/                  # 前端静态资源
│  ├─ package.json
│  ├─ pnpm-lock.yaml
│  └─ .env.example             # 环境变量模板
├─ backend/                    # FastAPI 服务
│  ├─ app/main.py              # API 入口
│  ├─ app/services/data.py     # 数据读取
│  ├─ app/services/model.py    # 模型加载与推理
│  └─ tests/                   # 后端测试
├─ ml/                         # 数据处理、训练和评估脚本
├─ data/                       # 台风数据集
│  ├─ year/                    # 年度索引
│  ├─ typhoon/                 # 历史轨迹 JSON
│  └─ processed/               # 处理后的数据集
├─ artifacts/checkpoints/      # 模型权重
├─ baogao.docx                # 项目报告
├─ IMPLEMENTATION_PLAN.md     # 实施和验收记录
└─ README.md
```

## 环境要求

- Windows 10/11
- Node.js 20 或更高版本
- pnpm 9 或更高版本
- Python 3.8 或更高版本
- 推荐使用 NVIDIA GPU 和 CUDA 12.1；没有 GPU 时可修改训练命令使用 CPU

## 安装依赖

### 前端

```powershell
cd D:\project\CNN-Cesium\CNN-Cesium
corepack enable
corepack prepare pnpm@9 --activate
pnpm install --frozen-lockfile
```

如果 PowerShell 提示找不到 `pnpm`，关闭并重新打开终端，或使用：

```powershell
corepack pnpm install --frozen-lockfile
```

### 后端和模型

```powershell
cd D:\project\CNN-Cesium
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
python -m pip install -r ml\requirements.txt
```

`ml/requirements.txt` 默认安装 CUDA 12.1 版本的 PyTorch。若只使用 CPU，可根据本机 Python 和 PyTorch 官方安装命令替换该依赖。

## 配置 Cesium Token

复制环境变量模板：

```powershell
cd D:\project\CNN-Cesium\CNN-Cesium
Copy-Item .env.example .env.dev
```

在 `.env.dev` 中设置：

```dotenv
VITE_CESIUM_TOKEN=你的_Cesium_ION_Token
VITE_HTTP_PROXY=Y
VITE_API_PROXY_TARGET=http://127.0.0.1:8000
```

本仓库为私有仓库，但 `.env` 中的 Token 仍属于敏感凭据。仓库一旦公开、转移或授权给其他人员，应立即撤销旧 Token 并重新生成。

## 启动项目

### 1. 启动后端

在项目根目录执行：

```powershell
cd D:\project\CNN-Cesium
.\.venv\Scripts\Activate.ps1
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

后端地址：<http://127.0.0.1:8000>

健康检查：<http://127.0.0.1:8000/health>

### 2. 启动前端

另开一个 PowerShell 窗口：

```powershell
cd D:\project\CNN-Cesium\CNN-Cesium
pnpm dev
```

浏览器访问：<http://127.0.0.1:10060/>

前端开发服务器会将 `/api` 请求代理到 `http://127.0.0.1:8000`。如果只启动前端而未启动后端，页面可以打开，但查询和预测接口不可用。

## API 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 检查 API、数据和模型状态 |
| GET | `/api/years.json` | 获取年份列表 |
| GET | `/api/typhoons` | 获取台风索引 |
| GET | `/api/typhoons/{id}` | 获取单个台风详情 |
| GET | `/api/typhoons/{id}/points` | 获取轨迹点 |
| GET | `/api/{id}.json` | 兼容前端的台风详情接口 |
| POST | `/api/predict` | 预测未来路径和风速 |

预测接口要求输入连续的历史观测点，前端会自动使用当前台风数据构造请求。接口支持的预测时效为 `6、12、18、24、30、36` 小时。

## 数据处理与训练

数据审计：

```powershell
cd D:\project\CNN-Cesium
.\.venv\Scripts\python.exe ml\data_audit.py
```

构建 6 小时重采样数据集：

```powershell
.\.venv\Scripts\python.exe -m ml.prepare_dataset
```

训练模型：

```powershell
.\.venv\Scripts\python.exe -m ml.train --device auto
```

训练产物保存到 `artifacts/`。当前后端默认加载：

```text
artifacts/checkpoints/track_cnn_baseline.pth
```

`CNN-Cesium/model.pth` 是历史目录中的空占位文件，不是运行所需权重。运行预测时以后端实际加载结果和 `/health` 返回状态为准。

## 前端检查与构建

```powershell
cd D:\project\CNN-Cesium\CNN-Cesium
pnpm lint
pnpm build
pnpm preview
```

生产构建输出在 `CNN-Cesium/dist/`。部署时需要将 `/api` 反向代理到 FastAPI 服务，并正确配置 Cesium 静态资源和 Token。

## 已完成验收

- 后端单元测试：3/3 通过
- Python 编译检查：通过
- Vue/TypeScript 类型检查：通过
- ESLint：0 个错误
- 前端生产构建：通过
- FastAPI `/health`：HTTP 200
- 真实 `/api/predict`：HTTP 200，返回 6-36 小时预测
- Cesium 历史轨迹和预测轨迹：正常显示
- 390px 窄屏：无横向溢出

## 当前模型结果说明

当前模型是可运行的 CNN 基线，不代表所有预测时效都优于传统方法。项目报告和答辩材料中的指标应以 `artifacts/` 中的实际实验日志和测试结果为准，不应把规划中的气象格点融合、概率锥体或不确定性区间描述成已经完成的功能。

后续算法改进方向包括：轨迹与气象格点双分支融合、局地坐标残差预测、地理距离损失、多步解码和不确定性校准。每项改进都应在相同数据划分和训练预算下进行消融实验。

## 许可证与材料

本仓库用于课程项目和研究演示。数据来源、报告、PPT/PDF 及第三方依赖的许可和使用范围，以各自原始来源及项目材料说明为准。
