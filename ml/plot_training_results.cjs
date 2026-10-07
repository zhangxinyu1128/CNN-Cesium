const fs = require('node:fs');
const path = require('node:path');
const echarts = require('../CNN-Cesium/node_modules/echarts');

const root = path.resolve(__dirname, '..');
const reportDir = path.join(root, 'artifacts', 'reports');
const readJson = (relativePath) => JSON.parse(fs.readFileSync(path.join(root, relativePath), 'utf8'));

function readRun(rootPath) {
  const runsPath = path.join(root, rootPath, 'runs');
  const files = fs.readdirSync(runsPath)
    .map((name) => path.join(runsPath, name, 'test_metrics.json'))
    .filter((file) => fs.existsSync(file));
  if (files.length !== 1) throw new Error(`Expected one run under ${runsPath}; found ${files.length}`);
  return JSON.parse(fs.readFileSync(files[0], 'utf8'));
}

const baseline = readRun('artifacts/baseline_frozen');
const seeds = [
  readRun('artifacts/residual'),
  readRun('artifacts/residual_seed_sensitivity/seed_2027'),
  readRun('artifacts/residual_seed_sensitivity/seed_2028'),
];
const bootstrap = readJson('artifacts/residual/storm_bootstrap.json');
const uncertainty = readJson('artifacts/residual/uncertainty_clustered.json');
const leads = seeds[0].evaluation.cnn_residual.by_horizon.map((row) => `${row.lead_hours}h`);
const meanAtLead = (read, metric) => leads.map((_, index) => (
  seeds.reduce((sum, run) => sum + read(run, index), 0) / seeds.length
));
const residualPath = meanAtLead((run, i) => run.evaluation.cnn_residual.by_horizon[i].path_mae_km);
const residualWind = meanAtLead((run, i) => run.evaluation.cnn_residual.by_horizon[i].wind_mae_ms);
const powerGain = ['low', 'medium', 'high'].map((group) => leads.map((_, index) => (
  ['2026', '2027', '2028'].reduce(
    (sum, seed) => sum + bootstrap.seeds[seed].power_tertiles[group].by_horizon[index].mean_paired_improvement_km,
    0,
  ) / 3
)));
const ciPositive = leads.every((_, index) => ['2026', '2027', '2028'].every(
  (seed) => bootstrap.seeds[seed].by_horizon[index].ci_excludes_zero,
));
if (!ciPositive) throw new Error('Overall bootstrap CIs must all exclude zero before plotting');

const ink = '#20313b';
const muted = '#60727a';
const gridColor = '#e7edf0';
const colors = ['#2e7d70', '#dc7847', '#4f7db8', '#9a6db0', '#b38b2e'];
const style = {
  color: ink,
  fontFamily: 'Microsoft YaHei, Noto Sans CJK SC, sans-serif',
  fontSize: 12,
};
const axis = {
  axisLine: { lineStyle: { color: '#9baab0' } },
  axisTick: { show: false },
  axisLabel: { color: muted, fontSize: 11 },
  splitLine: { show: false },
};
const valueAxis = {
  ...axis,
  type: 'value',
  splitLine: { show: true, lineStyle: { color: gridColor, type: 'dashed' } },
};
const titleStyle = { ...style, color: ink, fontSize: 16, fontWeight: 600 };
const legendStyle = { textStyle: { ...style, color: muted, fontSize: 10 }, itemWidth: 12, itemHeight: 8 };
const options = {
  backgroundColor: '#ffffff',
  animation: false,
  textStyle: style,
  title: [
    { text: '路径误差随预报时效', left: 72, top: 16, textStyle: titleStyle },
    { text: '风速误差随预报时效', left: 725, top: 16, textStyle: titleStyle },
    { text: '台风级 90% 位置校准', left: 72, top: 485, textStyle: titleStyle },
    { text: '按输入 power 分层的路径改善', left: 725, top: 485, textStyle: titleStyle },
  ],
  legend: [
    { ...legendStyle, left: 70, top: 48, data: ['Persistence', '匀速外推', '原始 CNN', '残差 CNN（三种子均值）'] },
    { ...legendStyle, left: 725, top: 48, data: ['Persistence', '残差 CNN（三种子均值）'] },
    { ...legendStyle, left: 70, top: 517, data: ['位置半径（km）', '整场台风覆盖率'] },
    { ...legendStyle, left: 725, top: 517, data: ['低 power', '中 power', '高 power'] },
  ],
  grid: [
    { left: 68, top: 91, width: 570, height: 325, containLabel: true },
    { left: 720, top: 91, width: 580, height: 325, containLabel: true },
    { left: 68, top: 558, width: 570, height: 330, containLabel: true },
    { left: 720, top: 558, width: 580, height: 330, containLabel: true },
  ],
  xAxis: [0, 1, 2, 3].map((gridIndex) => ({
    ...axis,
    gridIndex,
    type: 'category',
    boundaryGap: gridIndex === 2,
    data: leads,
  })),
  yAxis: [
    { ...valueAxis, gridIndex: 0, name: 'km', nameTextStyle: { color: muted } },
    { ...valueAxis, gridIndex: 1, name: 'm/s*', nameTextStyle: { color: muted } },
    { ...valueAxis, gridIndex: 2, name: 'km', nameTextStyle: { color: muted } },
    { ...valueAxis, gridIndex: 2, name: 'storm coverage', position: 'right', min: 0, max: 1, axisLabel: { ...axis.axisLabel, formatter: (value) => `${Math.round(value * 100)}%` }, splitLine: { show: false } },
    { ...valueAxis, gridIndex: 3, name: 'km improvement', nameTextStyle: { color: muted } },
  ],
  series: [
    {
      name: 'Persistence', type: 'line', xAxisIndex: 0, yAxisIndex: 0, legendIndex: 0,
      data: seeds[0].evaluation.baselines.last_observation.by_horizon.map((row) => row.path_mae_km),
      symbol: 'none', lineStyle: { width: 2, color: colors[2], type: 'dashed' },
    },
    {
      name: '匀速外推', type: 'line', xAxisIndex: 0, yAxisIndex: 0, legendIndex: 0,
      data: seeds[0].evaluation.baselines.constant_velocity.by_horizon.map((row) => row.path_mae_km),
      symbol: 'none', lineStyle: { width: 2, color: colors[1] },
    },
    {
      name: '原始 CNN', type: 'line', xAxisIndex: 0, yAxisIndex: 0, legendIndex: 0,
      data: baseline.evaluation.cnn.by_horizon.map((row) => row.path_mae_km),
      symbol: 'none', lineStyle: { width: 2, color: colors[3] },
    },
    {
      name: '残差 CNN（三种子均值）', type: 'line', xAxisIndex: 0, yAxisIndex: 0, legendIndex: 0,
      data: residualPath, symbol: 'circle', symbolSize: 7, lineStyle: { width: 3, color: colors[0] },
      itemStyle: { color: colors[0] },
    },
    {
      name: 'Persistence', type: 'line', xAxisIndex: 1, yAxisIndex: 1, legendIndex: 1,
      data: seeds[0].evaluation.baselines.last_observation.by_horizon.map((row) => row.wind_mae_ms),
      symbol: 'none', lineStyle: { width: 2, color: colors[2], type: 'dashed' },
    },
    {
      name: '残差 CNN（三种子均值）', type: 'line', xAxisIndex: 1, yAxisIndex: 1, legendIndex: 1,
      data: residualWind, symbol: 'circle', symbolSize: 7, lineStyle: { width: 3, color: colors[0] },
      itemStyle: { color: colors[0] },
    },
    {
      name: '位置半径（km）', type: 'bar', xAxisIndex: 2, yAxisIndex: 2, legendIndex: 2,
      data: uncertainty.evaluation.by_horizon.map((row) => row.location_radius_90_km),
      barWidth: 20, itemStyle: { color: colors[4], borderRadius: [2, 2, 0, 0] },
    },
    {
      name: '整场台风覆盖率', type: 'line', xAxisIndex: 2, yAxisIndex: 3, legendIndex: 2,
      data: uncertainty.evaluation.by_horizon.map((row) => row.location_storm_coverage_90),
      symbol: 'circle', symbolSize: 6, lineStyle: { width: 2, color: colors[0] },
      itemStyle: { color: colors[0] },
    },
    ...['low', 'medium', 'high'].map((group, index) => ({
      name: `${['低', '中', '高'][index]} power`, type: 'line', xAxisIndex: 3, yAxisIndex: 4, legendIndex: 3,
      data: powerGain[index], symbol: 'circle', symbolSize: 6,
      lineStyle: { width: 2, color: colors[index] }, itemStyle: { color: colors[index] },
    })),
  ],
  graphic: [
    { type: 'text', left: 68, top: 921, style: { text: 'Test: 2020-2025; 1,670 windows / 114 storms.', fill: muted, font: '12px sans-serif' } },
    { type: 'text', left: 68, top: 943, style: { text: 'Radius is storm-cluster calibrated; realized storm coverage can differ from 90%. Wind unit m/s awaits source documentation.', fill: muted, font: '12px sans-serif' } },
  ],
};

const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 1400, height: 970 });
chart.setOption(options);
const svg = chart.renderToSVGString();
fs.mkdirSync(reportDir, { recursive: true });
fs.writeFileSync(path.join(reportDir, 'training_results.svg'), svg, 'utf8');
console.log(`Wrote ${path.join(reportDir, 'training_results.svg')} (${svg.length} bytes)`);
chart.dispose();
