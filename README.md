# 基于 CNN 与 Cesium 的热带气旋可视化与预测系统

一个面向热带气旋历史轨迹分析、CNN 路径与强度预测以及 Cesium 三维展示的前后端项目。

项目包含前端源码、FastAPI 后端、训练脚本、数据集、模型权重和课程材料。推荐按“先启动后端，再启动前端”的顺序运行。

## 项目能力

- 按年份、编号和名称查询热带气旋历史数据
- Cesium 三维地球展示历史轨迹、当前点和预测轨迹
- 基于局地运动坐标的 CNN 多步预测未来 6、12、18、24、30、36 小时路径与风速
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

训练物理坐标残差模型：

```powershell
.\.venv\Scripts\python.exe -m ml.train_residual --device auto
```

### 72 小时多尺度模型实验（离线候选，不自动替换在线模型）

当前项目已使用局地东—北运动坐标编码。多尺度实验将历史窗口扩展到 12 个六小时步长，并分别比较多尺度残差 CNN、加入速度/加速度/转向约束的版本，以及 P10/P50/P90 分位数版本。它们写入独立的数据目录和实验目录，在线 API 继续使用已验收的四步 checkpoint，直到长窗口 API、固定测试集和校准结果单独通过验收。

```powershell
# 保留现有 data/processed 与在线模型使用的数据不变
.venv\Scripts\python.exe -m ml.prepare_dataset --input-steps 12 --output-root data\processed_12 --split-file data\splits\storm_splits_multiscale.json
.venv\Scripts\python.exe -m ml.train_multiscale --processed-root data\processed_12 --device auto
```

默认使用 2026、2027、2028 三个随机种子，冻结同一台风分组测试集。训练提前停止只使用验证台风的一半；另一半独立用于台风分组 split conformal 半径校准，测试集只用于最终报告。结果包含 6–36 小时逐时效误差、区间覆盖率和宽度、参数量及训练耗时，保存在 `artifacts/multiscale/runs/`。按论文口径解释结果时，应以每个台风为统计单位，并同时报告逐窗口与整场台风覆盖率。

该多尺度实验当前不会被 `/api/predict` 加载。现有在线接口仍要求 5 个连续观测点（形成 4 个历史特征步），需要完成输入窗口契约、checkpoint 兼容和浏览器端到端验收后，才可切换线上模型。ERA5 环境场当前属于单独的历史离线实验；本多尺度训练脚本不读取环境场，也不声称已实现缺失环境数据时的门控回退。

ERA5 增强版本还提供 `ml.models.gated_era5_fusion.GatedERA5Fusion` 组件。它只有在样本通过时间对齐并且 `era5_mask=1` 时才加入环境特征；缺失或不匹配时自动退回轨迹特征。该组件是后续实验接口，当前没有接入在线预测服务。

数据集输入为东向/北向位移、风速、强度、加速度、转向率和航向角正余弦；目标为相对最新观测点的东向/北向位移与风速。后端推理时会把位移还原为经纬度。

训练产物保存到 `artifacts/residual/`。使用新模型时设置：

```powershell
$env:TC_MODEL_PATH = 'D:\project\CNN-Cesium\artifacts\residual\checkpoints\track_cnn_residual.pth'
```

`/health` 的 `model.coordinate_system` 会报告当前模型的坐标格式。

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

主实验使用按台风编号和年份隔离的训练、验证、测试集（训练 1945-2016，验证 2017-2019，测试 2020-2025），测试集包含 154 场台风和 1,670 个窗口。残差 CNN 以匀速运动为先验，在固定测试集上 6-36 小时路径误差均低于匀速外推，三种随机种子方向一致。主实验指标保存在 `artifacts/residual/runs/` 和 `artifacts/residual_seed_sensitivity/`。

项目已完成 ERA5 500/850 hPa 风场匹配和融合消融。扩展测试子集含 1,661 个配对窗口、112 场台风；三种子均值下，500/850 hPa 融合路径 MAE 在 6/12/18/24/30/36 小时分别为 41.93/81.18/127.01/179.85/239.52/306.65 km，轨迹-only 对照分别为 44.17/86.63/134.35/187.28/246.63/312.25 km。结果仅适用于当前 ERA5 配对历史测试集，不能外推为实时业务预报结论。

在线默认 API 加载 `artifacts/checkpoints/track_cnn_physics_multiscale.pth`，采用 12 步局地东-北运动特征、多尺度因果残差 CNN 和物理约束训练。前端按健康检查报告自动构造 13 个六小时观测点。该模型在 914 个留出台风窗口上低于旧基线误差；与原残差 CNN 的差异未达到统计显著。位置半径来自独立验证台风校准，目标覆盖率为 90%，不是灾害概率、实际影响半径或实时预报保证。设置 `TC_MODEL_PATH` 可切回旧模型。
阶段一已补充可追溯的 JTWC 官方预报数据：原始 b-deck/f-deck 保存在本地 `official_forecast/JTWC/`，因数据量较大不随普通源码提交；汇总表和配对误差位于 `official_forecast/forecast_points.csv`、`official_forecast/best_track_points.csv`、`official_forecast/paired_errors.csv`。当前冻结测试集已完成 JTWC 2025 f-deck 同起报对比，覆盖 26 个台风、471 个测试窗口；同一最佳路径真值下，官方 12/24/36 小时平均位置误差为 48.6/67.2/84.0 km。官方预报只覆盖 2025 年，且 f-deck 没有 6/18/30 小时点，不能据此声称模型全面优于官方预报。完整口径见 `official_forecast/说明.txt` 和 `artifacts/era5/annular_steering_flow/official_comparison/official_comparison.json`。

ERA5 来源为 [Copernicus Climate Change Service ERA5 pressure levels](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-pressure-levels)，来源登记和原始文件元数据见 `ERA5data/SOURCE.json` 与 `ERA5data/metadata.json`。轨迹对齐、配对样本和实验指标保存在 `artifacts/era5/historical_500_850_refresh_20261002/`。原始 GRIB 和派生 JSONL 属于本地数据材料，不随普通 GitHub 源码提交；按项目数据清单准备后可复现实验。

ERA5 解析环境配置文件为 `ml/requirements-era5.txt`。核验命令：

```powershell
cd D:\project\CNN-Cesium
.venv\Scripts\python.exe ml\inspect_era5_grib.py --verify
```

## 许可证与材料

本仓库用于课程项目和研究演示。数据来源、报告、PPT/PDF 及第三方依赖的许可和使用范围，以各自原始来源及项目材料说明为准。
