# 后端服务

FastAPI 提供历史数据、健康检查和 checkpoint-backed CNN 预测接口。默认模型为 `artifacts/checkpoints/track_cnn_physics_multiscale.pth`，使用 12 步局地运动特征、多尺度因果残差 CNN 和物理约束训练；原有 `CNN-Cesium/model.pth` 是 0 字节旧文件，不使用。

在项目根目录准备包含 FastAPI、PyTorch 和 NumPy 的 Python 环境：

```powershell
py -3.8 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt -r ml\requirements.txt
python -m uvicorn backend.app.main:app --reload --port 8000
```

默认预测输入必须包含 13 个时间递增且相隔 6 小时的历史观测：前置点用于计算运动特征，后 12 点还需提供 `speed` 和 `power`。模型输出 6、12、18、24、30、36 小时结果；可在 `horizons_hours` 中请求其中的有序子集。`/health` 会报告当前 checkpoint 所需的 `input_steps` 和 `history_points`。数据中的无时区时间按照 UTC 处理。默认 checkpoint 与验证集台风分组共形校准记录按 SHA-256 匹配，响应会包含 `location_radius_90_km` 和圆形历史校准区域；该半径目标覆盖率为 90%，并非实时保证或灾害概率。

接口：

```text
GET  http://127.0.0.1:8000/health
GET  http://127.0.0.1:8000/api/years.json
GET  http://127.0.0.1:8000/api/typhoons?year=2024&limit=20
GET  http://127.0.0.1:8000/api/typhoons/202426
POST http://127.0.0.1:8000/api/predict
```

可通过环境变量覆盖数据、权重和设备：

```powershell
$env:TC_DATA_ROOT = 'D:\project\CNN-Cesium\data'
$env:TC_MODEL_PATH = 'D:\project\CNN-Cesium\artifacts\checkpoints\track_cnn_baseline.pth'
$env:TC_DEVICE = 'auto' # auto, cuda, cpu
```

要切回旧的四步基线，在启动后端前设置：

```powershell
$env:TC_MODEL_PATH = 'D:\project\CNN-Cesium\artifacts\residual\checkpoints\track_cnn_residual.pth'
python -m uvicorn backend.app.main:app --reload --port 8000
```

切换权重后需重启后端。`/health` 的 `model` 会显示模型类型、坐标系统、输入步数与权重哈希。新模型在 914 个留出台风窗口上低于旧基线误差，但相对原残差 CNN 的差异未达到统计显著；模型接入本身不代表业务预报已验证。

运行单元/集成测试：

```powershell
python -m unittest discover -s backend\tests -v
```
