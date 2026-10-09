<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
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

function baseOption(name: string, color: string, unit: string, values: Array<number | null>): EChartsOption {
  return {
    animation: false,
    grid: { left: 42, right: 12, top: 30, bottom: 30 },
    tooltip: {
      show: true,
      trigger: 'axis',
      triggerOn: 'mousemove|click',
      renderMode: 'html',
      confine: true,
      axisPointer: {
        type: 'line',
        lineStyle: { color: '#f7c873', width: 1, type: 'dashed' }
      },
      backgroundColor: 'rgba(3, 24, 38, 0.96)',
      borderColor: '#67e8d0',
      borderWidth: 1,
      textStyle: { color: '#f5fbff', fontSize: 12 },
      formatter: (params: unknown) => {
        const item = (Array.isArray(params) ? params[0] : params) as {
          dataIndex?: number
          axisValue?: string
          axisValueLabel?: string
        } | undefined
        const index = typeof item?.dataIndex === 'number' ? item.dataIndex : -1
        const value = index >= 0 ? values[index] : null
        const time = index >= 0 ? labels.value[index] : item?.axisValueLabel ?? item?.axisValue ?? '--'
        const displayValue = typeof value === 'number' ? value.toFixed(1) : '--'
        return `<strong>${time}</strong><br/>${name}：${displayValue} ${unit}`
      }
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
      showSymbol: true,
      symbol: 'circle',
      symbolSize: 4,
      lineStyle: { width: 2.5, color },
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
    speedChart.value!.setOption(baseOption('风速', '#e46856', 'm/s', speedValues.value), true)
  }
  if (pressureEl.value && hasPressure.value) {
    pressureChart.value ??= echarts.init(pressureEl.value)
    const pressureOption = baseOption('中心气压', '#3478c9', 'hPa', pressureValues.value)
    pressureChart.value!.setOption({
      ...pressureOption,
      yAxis: {
        ...(pressureOption.yAxis as object),
        min: pressureStats.value?.minAxis,
        max: pressureStats.value?.maxAxis,
        interval: 5,
        axisLabel: {
          color: '#718096',
          fontSize: 10,
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
