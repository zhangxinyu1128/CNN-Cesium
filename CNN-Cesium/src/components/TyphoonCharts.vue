<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { ECharts, EChartsOption } from 'echarts'
import type { TyphoonPoint } from '@/types/typhoon'

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const props = defineProps<{
  points: TyphoonPoint[]
  currentIndex: number
}>()

const speedEl = ref<HTMLDivElement | null>(null)
const pressureEl = ref<HTMLDivElement | null>(null)
const speedChart = ref<ECharts | null>(null)
const pressureChart = ref<ECharts | null>(null)

const labels = computed(() => props.points.map((point) => point.time.slice(5, 16).replace('T', ' ')))
const speedValues = computed(() => props.points.map((point) => point.speed ?? null))
const pressureValues = computed(() => props.points.map((point) => point.pressure ?? null))
const hasSpeed = computed(() => speedValues.value.some((value) => typeof value === 'number'))
const hasPressure = computed(() => pressureValues.value.some((value) => typeof value === 'number'))

function baseOption(name: string, color: string, unit: string, values: Array<number | null>): EChartsOption {
  return {
    animation: false,
    grid: { left: 42, right: 12, top: 30, bottom: 30 },
    tooltip: {
      trigger: 'axis',
      valueFormatter: (value) => value == null ? '--' : `${value} ${unit}`
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: labels.value,
      axisLabel: { color: '#718096', fontSize: 10, hideOverlap: true },
      axisLine: { lineStyle: { color: '#d8dee8' } }
    },
    yAxis: {
      type: 'value',
      name,
      nameTextStyle: { color: '#718096', fontSize: 10 },
      axisLabel: { color: '#718096', fontSize: 10 },
      splitLine: { lineStyle: { color: '#edf0f5' } }
    },
    series: [{
      type: 'line',
      name,
      data: values,
      smooth: 0.18,
      connectNulls: false,
      showSymbol: false,
      lineStyle: { width: 2, color },
      itemStyle: { color },
      markLine: {
        silent: true,
        symbol: 'none',
        data: [{ xAxis: props.currentIndex }],
        lineStyle: { color: '#1f2937', type: 'dashed', width: 1 }
      }
    }]
  }
}

function render() {
  if (speedEl.value && hasSpeed.value) {
    speedChart.value ??= echarts.init(speedEl.value)
    speedChart.value.setOption(baseOption('风速', '#e46856', 'm/s', speedValues.value), true)
  }
  if (pressureEl.value && hasPressure.value) {
    pressureChart.value ??= echarts.init(pressureEl.value)
    pressureChart.value.setOption({
      ...baseOption('中心气压', '#3478c9', 'hPa', pressureValues.value),
      yAxis: { ...(baseOption('中心气压', '#3478c9', 'hPa', pressureValues.value).yAxis as object), inverse: false }
    }, true)
  }
}

function resize() {
  speedChart.value?.resize()
  pressureChart.value?.resize()
}

watch(() => [props.points, props.currentIndex], async () => {
  await nextTick()
  render()
}, { deep: true, immediate: true })

window.addEventListener('resize', resize)
onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  speedChart.value?.dispose()
  pressureChart.value?.dispose()
})
</script>

<template>
  <section class="history-charts" aria-label="台风历史气象曲线">
    <template v-if="hasSpeed">
      <div class="chart-heading"><span>风速演变</span><small>实况最佳路径 · m/s</small></div>
      <div ref="speedEl" class="history-chart"></div>
    </template>
    <template v-if="hasPressure">
      <div class="chart-heading"><span>中心气压演变</span><small>实况最佳路径 · hPa</small></div>
      <div ref="pressureEl" class="history-chart"></div>
    </template>
    <p v-if="!hasSpeed && !hasPressure" class="history-empty">当前台风没有可用的风速或中心气压序列。</p>
  </section>
</template>
