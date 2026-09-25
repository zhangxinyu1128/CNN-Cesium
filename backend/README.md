# 后端服务

FastAPI 提供历史数据、健康检查和 checkpoint-backed CNN 预测接口。默认模型位于 `artifacts/checkpoints/track_cnn_baseline.pth`；原有 `CNN-Cesium/model.pth` 是 0 字节旧文件，不使用。

在项目根目录准备包含 FastAPI、PyTorch 和 NumPy 的 Python 环境：

```powershell
py -3.8 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt -r ml\requirements.txt
python -m uvicorn backend.app.main:app --reload --port 8000
```

预测输入必须包含五个时间递增且相隔 6 小时的历史观测。第一个点用于计算位移特征，后四个点需要提供 `speed` 和 `power`。模型输出 6、12、18、24、30、36 小时结果；可在 `horizons_hours` 中请求其中的有序子集。数据中的无时区时间按照 UTC 处理。预测区间尚未校准，`p05` 和 `p95` 为 `null`。

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

运行单元/集成测试：

```powershell
python -m unittest discover -s backend\tests -v
```
