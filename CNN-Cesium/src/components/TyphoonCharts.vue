<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsType as ECharts } from 'echarts/core'
import type { EChartsOption } from 'echarts'
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
const pressureStats = computed(() => {
  const values = pressureValues.value.filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  if (!values.length) return null
  const min = Math.min(...values)
  const max = Math.max(...values)
  const padding = Math.max(5, Math.ceil((max - min) * 0.2 / 5) * 5)
  return {
    minAxis: Math.floor((min - padding) / 5) * 5,
    maxAxis: Math.ceil((max + padding) / 5) * 5,
    lowest: min,
    range: max - min
  }
})

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  })[character]!)
}

function formatValue(value: number | null | undefined, unit = '') {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${value.toFixed(1)}${unit ? ` ${unit}` : ''}`
    : '--'
}

function alphaColor(hex: string, alpha: number) {
  const value = hex.replace('#', '')
  const r = parseInt(value.slice(0, 2), 16)
  const g = parseInt(value.slice(2, 4), 16)
  const b = parseInt(value.slice(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

function baseOption(name: string, color: string, values: Array<number | null>): EChartsOption {
  return {
    animation: false,
    grid: { left: 42, right: 12, top: 30, bottom: 30 },
    tooltip: {
      show: true,
      trigger: 'axis',
      triggerOn: 'mousemove',
      renderMode: 'html',
      confine: true,
      axisPointer: {
        type: 'line',
        snap: true,
        lineStyle: { color: '#f7c873', width: 1, type: 'dashed' }
      },
      backgroundColor: 'rgba(3, 24, 38, 0.96)',
      borderColor: color,
      borderWidth: 1,
      padding: [9, 11],
      textStyle: { color: '#f5fbff', fontSize: 13 },
      formatter: (params: unknown) => {
        const item = (Array.isArray(params) ? params[0] : params) as {
          dataIndex?: number
          axisValue?: string
          axisValueLabel?: string
        } | undefined
        const index = typeof item?.dataIndex === 'number' ? item.dataIndex : -1
        const point = index >= 0 ? props.points[index] : undefined
        const time = point?.time ? escapeHtml(point.time.replace('T', ' ')) : escapeHtml(String(item?.axisValueLabel ?? item?.axisValue ?? '--'))
        const rows = [
          ['风速', formatValue(point?.speed, 'm/s')],
          ['中心气压', formatValue(point?.pressure, 'hPa')],
          ['位置', point ? `${formatValue(point.lat, '°N')} · ${formatValue(point.lng, '°E')}` : '--'],
          ['强度', point?.strong ? escapeHtml(point.strong) : '--']
        ]
        return `<div style="min-width:150px;color:#f5fbff"><strong style="display:block;margin-bottom:6px;color:#8fe8ff">${time}</strong>${rows.map(([label, value]) => `<div style="display:flex;justify-content:space-between;gap:18px;padding:2px 0"><span style="color:#9db8c8">${label}</span><span>${value}</span></div>`).join('')}</div>`
      }
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: labels.value,
      axisLabel: { color: '#8aa3b5', fontSize: 12, hideOverlap: true },
      axisLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.28)' } }
    },
    yAxis: {
      type: 'value',
      name,
      nameTextStyle: { color: '#8aa3b5', fontSize: 12 },
      axisLabel: { color: '#8aa3b5', fontSize: 12 },
      splitLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.10)' } }
    },
    series: [{
      type: 'line',
      name,
      data: values,
      smooth: true,
      connectNulls: false,
      showSymbol: false,
      symbol: 'circle',
      symbolSize: 5,
      lineStyle: { width: 2, color },
      itemStyle: { color },
      areaStyle: {
        color: {
          type: 'linear',
          x: 0,
          y: 0,
          x2: 0,
          y2: 1,
          colorStops: [
            { offset: 0, color: alphaColor(color, 0.42) },
            { offset: 1, color: alphaColor(color, 0.02) }
          ]
        }
      },
      emphasis: {
        focus: 'series',
        scale: 1.6,
        itemStyle: { borderColor: '#fff', borderWidth: 2 }
      },
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
    speedChart.value!.setOption(baseOption('风速', '#e46856', speedValues.value), true)
  }
  if (pressureEl.value && hasPressure.value) {
    pressureChart.value ??= echarts.init(pressureEl.value)
    const pressureOption = baseOption('中心气压', '#3478c9', pressureValues.value)
    pressureChart.value!.setOption({
      ...pressureOption,
      yAxis: {
        ...(pressureOption.yAxis as object),
        min: pressureStats.value?.minAxis,
        max: pressureStats.value?.maxAxis,
        interval: 5,
        axisLabel: {
          color: '#8aa3b5',
          fontSize: 12,
          formatter: (value: number) => `${value}`
        }
      }
    }, true)
  }
}

function resize() {
  speedChart.value?.resize()
  pressureChart.value?.resize()
}

let resizeObserver: ResizeObserver | null = null

function observeCharts() {
  if (!resizeObserver) return
  resizeObserver.disconnect()
  if (speedEl.value) resizeObserver.observe(speedEl.value)
  if (pressureEl.value) resizeObserver.observe(pressureEl.value)
}

watch(() => [props.points, props.currentIndex], async () => {
  await nextTick()
  render()
  observeCharts()
}, { deep: true, immediate: true })

onMounted(() => {
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => resize())
  }
  observeCharts()
})

window.addEventListener('resize', resize)
onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  resizeObserver?.disconnect()
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
      <div class="chart-heading">
        <span>中心气压演变</span>
        <small v-if="pressureStats">最低 {{ pressureStats.lowest }} hPa · 全程变化 {{ pressureStats.range.toFixed(0) }} hPa</small>
        <small v-else>实况最佳路径 · hPa</small>
      </div>
      <div ref="pressureEl" class="history-chart"></div>
    </template>
    <p v-if="!hasSpeed && !hasPressure" class="history-empty">当前台风没有可用的风速或中心气压序列。</p>
  </section>
</template>
