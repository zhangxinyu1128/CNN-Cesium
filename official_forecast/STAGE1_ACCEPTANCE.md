# 阶段一官方数据接入验收

## 结论

阶段一官方预报数据接入已完成。原始文件、标准化表、配对规则、误差结果和模型对比结果均已落盘，后端接口已经返回可追溯的官方对比状态，主页面图表会显示 JTWC 官方预报曲线。

## 已交付

| 产物 | 内容 |
| --- | --- |
| JTWC/best_track/2017-2024/ | 2017—2024 JTWC b-deck 原始最佳路径 |
| JTWC/forecast/2025_fst/ | 2025 JTWC f-deck 原始压缩包 |
| IBTrACS/ibtracs.WP.list.v04r01.csv | NOAA IBTrACS 西北太平洋原始真值补充 |
| forecast_points.csv | 1749 个官方预报点，保留 12/24/36 小时 |
| best_track_points.csv | 11810 个最佳路径真值点 |
| paired_errors.csv | 1475 个官方预报与真值配对点 |
| build_report.json | 原始数据整理统计和规则记录 |
| ml/build_official_forecast_tables.py | 可重复生成标准表和误差表的脚本 |
| artifacts/era5/annular_steering_flow/official_comparison/official_comparison.json | 模型与 JTWC 官方预报同测试集对比 |

## 实测验收

- JTWC 官方预报技术标识筛选：JTWC
- 起报时刻：00/06/12/18 UTC
- 预报位置不插值
- 真值最大允许插值间隔：9 小时
- 模型/官方共有时效：12/24/36 小时
- 官方对比测试窗口：471
- 官方对比台风数：26
- 官方平均位置误差：12h 48.6 km，24h 67.2 km，36h 84.0 km
- 官方曲线与模型曲线由同一测试窗口、同一真值表计算

## 限制

当前可访问的 JTWC f-deck 历史归档只覆盖 2025 年；2020—2024 年官方预报仍未补齐。原始 f-deck 不含 6/18/30 小时位置，因此主页面官方曲线只显示 12/24/36 小时。2025 年验证真值来自审计后的 best_track_points.csv，不把 f-deck tau=0 记录当作独立最佳路径。

## 重跑

    cd D:\project\CNN-Cesium
    .venv\Scripts\python.exe ml\build_official_forecast_tables.py
    .venv\Scripts\python.exe -m ml.evaluate_official_forecast --device cpu
