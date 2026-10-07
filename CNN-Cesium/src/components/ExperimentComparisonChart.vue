<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { ECharts, EChartsOption } from 'echarts'
import type { ExperimentModelSummary } from '@/types/typhoon'

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const props = defineProps<{
  models: ExperimentModelSummary[]
  horizon: number
  metric: 'mae' | 'rmse'
}>()

const chartEl = ref<HTMLDivElement | null>(null)
const chart = ref<ECharts | null>(null)
const colors = ['#67e8d0', '#f7c873', '#ff806f', '#76a9fa', '#c084fc', '#fda4af']

function render() {
  if (!chartEl.value || !props.models.length) return
  chart.value ??= echarts.init(chartEl.value)
  const horizons = [6, 12, 18, 24, 30, 36]
  const metricKey = props.metric === 'rmse' ? 'path_rmse_km' : 'path_mae_km'
  const metricLabel = props.metric === 'rmse' ? '路径 RMSE / km' : '路径 MAE / km'
  const option: EChartsOption = {
    animation: false,
    grid: { left: 42, right: 12, top: 34, bottom: 34 },
    tooltip: {
      trigger: 'axis',
      valueFormatter: (value) => typeof value === 'number' ? `${value.toFixed(1)} km` : '--'
    },
    legend: {
      top: 0,
      left: 0,
      right: 0,
      type: 'scroll',
      textStyle: { color: '#b8d5d6', fontSize: 10 },
      itemWidth: 13,
      itemHeight: 8
    },
    xAxis: {
      type: 'category',
      data: horizons.map((hour) => `${hour}h`),
      axisLabel: { color: '#87a9ad', fontSize: 10 },
      axisLine: { lineStyle: { color: 'rgba(143, 207, 205, 0.2)' } },
      axisTick: { show: false }
    },
    yAxis: {
      type: 'value',
      name: metricLabel,
      nameTextStyle: { color: '#87a9ad', fontSize: 10 },
      axisLabel: { color: '#87a9ad', fontSize: 10 },
      splitLine: { lineStyle: { color: 'rgba(143, 207, 205, 0.1)' } }
    },
    series: props.models.map((model, index) => ({
      type: 'line',
      name: model.name,
      smooth: 0.18,
      showSymbol: true,
      symbolSize: 5,
      data: horizons.map((hour) => model.by_horizon.find((item) => item.lead_hours === hour)?.[metricKey] ?? null),
      lineStyle: { width: model.key === 'fusion_500_850' ? 3 : 2, color: colors[index % colors.length] },
      itemStyle: { color: colors[index % colors.length] },
      markLine: index === 0 ? {
        silent: true,
        symbol: 'none',
        data: [{ xAxis: `${props.horizon}h` }],
        lineStyle: { color: '#f7c873', type: 'dashed', width: 1 },
        label: { color: '#f7c873', formatter: `${props.horizon}h` }
      } : undefined
    }))
  }
  chart.value.setOption(option, true)
}

function resize() {
  chart.value?.resize()
}

watch(() => [props.models, props.horizon, props.metric], async () => {
  await nextTick()
  render()
}, { deep: true })

onMounted(render)
window.addEventListener('resize', resize)
onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart.value?.dispose()
})
</script>

<template>
  <div ref="chartEl" class="experiment-comparison-chart" :aria-label="`不同模型路径${metric === 'rmse' ? '均方根误差' : '平均绝对误差'}对比图`"></div>
</template>

<style scoped>
.experiment-comparison-chart {
  width: 100%;
  height: 226px;
}
</style>
