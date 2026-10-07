<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import TyphoonGlobe from '@/components/TyphoonGlobe.vue'
import TyphoonCharts from '@/components/TyphoonCharts.vue'
import {
  fetchExperimentSummary,
  fetchHealth,
  fetchTyphoon,
  fetchTyphoonEra5,
  fetchTyphoonIndex,
  fetchYears,
  predictTyphoon
} from '@/services/typhoonApi'
import { buildPredictionWindow } from '@/services/predictionWindow'
import type {
  HealthResponse,
  Era5Point,
  Era5Response,
  ExperimentModelSummary,
  ExperimentSummary,
  PredictionHistoryPoint,
  PredictionResponse,
  TyphoonDetail,
  TyphoonIndexItem,
  TyphoonPoint
} from '@/types/typhoon'
import '@/styles/index.scss'

const years = ref<number[]>([])
const globe = ref<{ resetView: () => void } | null>(null)
const selectedYear = ref<number | undefined>()
const query = ref('')
const landingOnly = ref(false)
const typhoons = ref<TyphoonIndexItem[]>([])
const selectedId = ref('')
const selectedTyphoon = ref<TyphoonDetail | null>(null)
const loadingList = ref(false)
const loadingDetail = ref(false)
const dataError = ref('')
const health = ref<HealthResponse | null>(null)
const healthError = ref('')
const activeTab = ref<'overview' | 'forecast' | 'comparison' | 'evaluation'>('overview')
const currentIndex = ref(0)
const playing = ref(false)
const showTrack = ref(true)
const showForecast = ref(true)
const showEra5 = ref(true)
const era5Level = ref(850)
const era5 = ref<Era5Response | null>(null)
const era5Error = ref('')
const experimentSummary = ref<ExperimentSummary | null>(null)
const prediction = ref<PredictionResponse | null>(null)
const predictionInput = ref<PredictionHistoryPoint[] | null>(null)
const predictionError = ref('')
const loadingPrediction = ref(false)
const selectedLead = ref(24)
const analysisHorizon = ref(24)
let playTimer: number | undefined

const points = computed<TyphoonPoint[]>(() => selectedTyphoon.value?.points ?? [])
const currentPoint = computed(() => points.value[currentIndex.value] ?? null)
const maxWind = computed(() => Math.max(...points.value.map((point) => point.speed ?? 0), 0))
const minPressure = computed(() => {
  const values = points.value
    .map((point) => point.pressure)
    .filter((value): value is number => typeof value === 'number' && value > 0)
  return values.length ? Math.min(...values) : null
})
const maxPower = computed(() => Math.max(...points.value.map((point) => point.power ?? 0), 0))
const landingCount = computed(() => selectedTyphoon.value?.land?.length ?? 0)
const currentLabel = computed(() => currentPoint.value?.strong || '未分级')
const filteredTyphoons = computed(() => {
  const text = query.value.trim().toLowerCase()
  return typhoons.value.filter((item) => {
    const searchable = `${item.tfbh} ${item.name ?? ''} ${item.ename ?? ''}`.toLowerCase()
    const matchesQuery = !text || searchable.includes(text)
    const matchesLanding = !landingOnly.value || (item.land?.length ?? 0) > 0
    return matchesQuery && matchesLanding
  })
})
const progress = computed(() =>
  points.value.length > 1 ? (currentIndex.value / (points.value.length - 1)) * 100 : 0
)
const predictionWindow = computed(() => buildPredictionWindow(points.value, currentIndex.value))
const canPredict = computed(() => health.value?.model.status === 'ready' && predictionWindow.value.ok)
const predictionWindowReason = computed(() =>
  predictionWindow.value.ok ? '' : predictionWindow.value.reason
)
const predictionAvailabilityMessage = computed(() => {
  if (healthError.value) return healthError.value
  if (!health.value) return '正在检查数据与模型服务。'
  if (health.value.model.status !== 'ready') {
    return health.value.model.reason || '当前模型不可用。'
  }
  return predictionWindowReason.value
})
const forecastOrigin = computed(() => predictionInput.value?.[predictionInput.value.length - 1] ?? null)
const selectedForecastPoint = computed(
  () => prediction.value?.predictions.find((item) => item.lead_hours === selectedLead.value) ?? null
)
const modelStatusLabel = computed(() => {
  if (healthError.value) return 'API 不可用'
  if (!health.value) return '模型状态检查中'
  return health.value.model.status === 'ready' ? 'CNN 已就绪' : '模型不可用'
})
const currentEra5 = computed<Era5Point | null>(() => {
  const target = canonicalTime(currentPoint.value?.time)
  if (!target || !era5.value?.points.length) return null
  return era5.value.points.find((point) => canonicalTime(point.time) === target) ?? null
})
const era5Levels = computed(() => era5.value?.levels_hpa ?? [])
const currentWind = computed(() => currentEra5.value?.levels[String(era5Level.value)] ?? null)
const currentShear = computed(() => currentEra5.value?.shear_500_850_ms ?? null)
const era5StatusLabel = computed(() => {
  if (era5Error.value) return '读取失败'
  if (!era5.value) return '同步中'
  if (era5.value.status !== 'ready') return '未匹配'
  return currentEra5.value ? '已同步' : '当前时刻缺测'
})
function canonicalTime(value?: string | null) {
  return value ? value.replace('Z', '').slice(0, 19) : ''
}

function metricAt(model: ExperimentModelSummary, lead = analysisHorizon.value) {
  return model.by_horizon.find((item) => item.lead_hours === lead) ?? model.by_horizon[0]
}

function uncertaintyAt(lead = analysisHorizon.value) {
  return experimentSummary.value?.uncertainty.by_horizon?.find((item) => item.lead_hours === lead)
}

function modelMetricText(key: string, field: 'path_mae_km' | 'path_rmse_km') {
  const model = experimentSummary.value?.models.find((item) => item.key === key)
  const metric = model ? metricAt(model) : undefined
  const value = metric?.[field]
  return typeof value === 'number' ? `${value.toFixed(1)} km` : '--'
}

function formatPercent(value?: number | null) {
  return typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '--'
}

function formatTime(value?: string) {
  if (!value) return '--'
  return value.replace('T', ' ')
}

function distanceKm(a: TyphoonPoint, b: TyphoonPoint) {
  const rad = Math.PI / 180
  const dLat = (b.lat - a.lat) * rad
  const dLng = (b.lng - a.lng) * rad
  const lat1 = a.lat * rad
  const lat2 = b.lat * rad
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2
  return 6371 * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h))
}

const trackDistance = computed(() => {
  let total = 0
  for (let index = 1; index < points.value.length; index += 1) {
    total += distanceKm(points.value[index - 1], points.value[index])
  }
  return Math.round(total)
})

async function loadYears() {
  try {
    years.value = await fetchYears()
    selectedYear.value = years.value[0]
  } catch (error) {
    dataError.value = error instanceof Error ? error.message : '年份数据加载失败'
  }
}

async function loadHealth() {
  try {
    health.value = await fetchHealth()
    healthError.value = ''
  } catch (error) {
    health.value = null
    healthError.value = error instanceof Error ? error.message : '健康状态获取失败'
  }
}

async function loadList() {
  if (!selectedYear.value) return
  loadingList.value = true
  dataError.value = ''
  try {
    typhoons.value = await fetchTyphoonIndex(selectedYear.value, query.value)
    const stillSelected = typhoons.value.some((item) => item.tfbh === selectedId.value)
    if (!stillSelected) {
      selectedId.value = typhoons.value[0]?.tfbh ?? ''
    }
  } catch (error) {
    dataError.value = error instanceof Error ? error.message : '台风列表加载失败'
    typhoons.value = []
  } finally {
    loadingList.value = false
  }
}

async function selectTyphoon(id: string) {
  if (!id) return
  selectedId.value = id
  prediction.value = null
  predictionInput.value = null
  predictionError.value = ''
  era5.value = null
  era5Error.value = ''
  loadingDetail.value = true
  try {
    selectedTyphoon.value = await fetchTyphoon(id)
    try {
      era5.value = await fetchTyphoonEra5(id)
      if (era5.value.levels_hpa.length && !era5.value.levels_hpa.includes(era5Level.value)) {
        era5Level.value = era5.value.levels_hpa[0]
      }
    } catch (error) {
      era5Error.value = error instanceof Error ? error.message : 'ERA5 风场加载失败'
    }
    const firstPredictableIndex = points.value.findIndex((_, index) =>
      buildPredictionWindow(points.value, index).ok
    )
    currentIndex.value = firstPredictableIndex >= 0 ? firstPredictableIndex : 0
    activeTab.value = 'overview'
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '台风详情加载失败')
    selectedTyphoon.value = null
  } finally {
    loadingDetail.value = false
  }
}

function togglePlay() {
  if (!points.value.length) return
  playing.value = !playing.value
  if (playing.value) {
    playTimer = window.setInterval(() => {
      if (currentIndex.value >= points.value.length - 1) {
        currentIndex.value = 0
      } else {
        currentIndex.value += 1
      }
    }, 700)
  } else if (playTimer) {
    window.clearInterval(playTimer)
    playTimer = undefined
  }
}

function resetTimeline() {
  playing.value = false
  if (playTimer) window.clearInterval(playTimer)
  playTimer = undefined
  currentIndex.value = 0
}

function resetGlobalView() {
  globe.value?.resetView()
}

function downloadTrack() {
  if (!selectedTyphoon.value) return
  const blob = new Blob([JSON.stringify(selectedTyphoon.value, null, 2)], {
    type: 'application/json'
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${selectedTyphoon.value.tfbh}-track.json`
  link.click()
  URL.revokeObjectURL(url)
}

async function requestPrediction() {
  if (!selectedTyphoon.value) return
  if (!health.value) await loadHealth()
  if (health.value?.model.status !== 'ready') {
    predictionError.value = health.value?.model.reason || healthError.value || '预测服务当前不可用。'
    return
  }
  const windowResult = predictionWindow.value
  if (!windowResult.ok) {
    predictionError.value = windowResult.reason
    return
  }

  loadingPrediction.value = true
  predictionError.value = ''
  try {
    const result = await predictTyphoon(selectedId.value, windowResult.history)
    predictionInput.value = windowResult.history
    prediction.value = result
    selectedLead.value = result.horizons_hours.includes(selectedLead.value)
      ? selectedLead.value
      : result.horizons_hours[0]
    showForecast.value = true
  } catch (error) {
    predictionError.value = error instanceof Error ? error.message : '预测请求失败'
    ElMessage.error(predictionError.value)
  } finally {
    loadingPrediction.value = false
  }
}

function downloadPrediction() {
  if (!prediction.value) return
  const blob = new Blob([JSON.stringify(prediction.value, null, 2)], {
    type: 'application/json'
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${selectedId.value}-cnn-forecast.json`
  link.click()
  URL.revokeObjectURL(url)
}

watch(selectedYear, loadList)
watch([query, landingOnly], () => {
  void loadList()
})
watch(selectedId, (id) => {
  void selectTyphoon(id)
})
watch(currentIndex, () => {
  prediction.value = null
  predictionInput.value = null
})

onMounted(async () => {
  void loadHealth()
  void fetchExperimentSummary().then((result) => {
    experimentSummary.value = result
  }).catch(() => {
    experimentSummary.value = null
  })
  await loadYears()
  await loadList()
})

onUnmounted(() => {
  if (playTimer) window.clearInterval(playTimer)
})
</script>

<template>
  <main class="app-shell">
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark">TC</div>
        <div>
          <h1>热带气旋监测台</h1>
          <p>Typhoon intelligence workspace</p>
        </div>
      </div>
      <div class="topbar-meta">
        <a class="home-link" href="/" title="返回主页面">返回主页面</a>
        <span class="status-dot" :class="{ 'is-offline': health?.data.status !== 'ready' }"></span>
        <span>{{ health?.data.status === 'ready' ? '历史数据在线' : '数据状态未知' }}</span>
        <span class="divider"></span>
        <span class="model-status" :class="{ 'is-ready': health?.model.status === 'ready' }">
          {{ modelStatusLabel }}
        </span>
      </div>
    </header>

    <section class="workspace">
      <aside class="sidebar left-panel">
        <div class="panel-heading">
          <div>
            <span class="eyebrow">ARCHIVE</span>
            <h2>台风档案</h2>
          </div>
          <span class="count-badge">{{ filteredTyphoons.length }}</span>
        </div>

        <div class="filter-stack">
          <label class="field-label" for="year">年份</label>
          <el-select id="year" v-model="selectedYear" placeholder="选择年份" filterable>
            <el-option v-for="year in years" :key="year" :label="String(year)" :value="year" />
          </el-select>

          <label class="field-label" for="query">检索</label>
          <el-input
            id="query"
            v-model="query"
            clearable
            placeholder="编号 / 中文名 / 英文名"
          >
            <template #prefix><span class="input-symbol">⌕</span></template>
          </el-input>

          <label class="check-row">
            <input v-model="landingOnly" type="checkbox" />
            <span>仅显示有登陆记录</span>
          </label>
        </div>

        <div class="list-caption">
          <span>{{ selectedYear ?? '--' }} 年记录</span>
          <span v-if="loadingList" class="loading-label">同步中</span>
        </div>

        <div class="typhoon-list" :class="{ 'is-loading': loadingList }">
          <button
            v-for="item in filteredTyphoons"
            :key="item.tfbh"
            class="typhoon-row"
            :class="{ active: item.tfbh === selectedId }"
            type="button"
            @click="selectedId = item.tfbh"
          >
            <span class="row-indicator"></span>
            <span class="row-main">
              <strong>{{ item.name || '未命名系统' }}</strong>
              <small>{{ item.tfbh }} · {{ item.ename || 'Unnamed' }}</small>
            </span>
            <span v-if="item.land?.length" class="land-mark" title="有登陆记录">↘</span>
          </button>
          <div v-if="!loadingList && !filteredTyphoons.length" class="list-empty">
            <span>暂无匹配记录</span>
          </div>
        </div>
      </aside>

      <section class="map-stage">
        <div class="map-toolbar">
          <button class="tool-button" type="button" title="回到全局视图" @click="resetGlobalView">
            <span>⌂</span>
            <small>全局</small>
          </button>
          <button
            class="tool-button"
            :class="{ selected: showTrack }"
            type="button"
            title="显示或隐藏轨迹"
            @click="showTrack = !showTrack"
          >
            <span>⌁</span>
            <small>轨迹</small>
          </button>
          <button
            class="tool-button"
            :class="{ selected: showForecast }"
            type="button"
            title="显示或隐藏预测路径"
            @click="showForecast = !showForecast"
          >
            <span>⌁</span>
            <small>预测</small>
          </button>
          <button
            class="tool-button"
            :class="{ selected: showEra5 }"
            type="button"
            title="显示或隐藏当前台风位置的 ERA5 风矢量"
            @click="showEra5 = !showEra5"
          >
            <span>↗</span>
            <small>ERA5</small>
          </button>
          <select v-model.number="era5Level" class="era5-level-select" :disabled="!era5Levels.length">
            <option v-for="level in era5Levels" :key="level" :value="level">{{ level }} hPa</option>
          </select>
          <button class="tool-button" type="button" title="导出台风数据" @click="downloadTrack">
            <span>⇩</span>
            <small>导出</small>
          </button>
        </div>
        <TyphoonGlobe
          ref="globe"
          :points="points"
          :current-index="currentIndex"
          :show-track="showTrack"
          :forecast-start="forecastOrigin"
          :predictions="showForecast ? prediction?.predictions ?? [] : []"
          :show-era5="showEra5"
          :era5-point="currentEra5"
          :era5-level="era5Level"
        />
        <div class="map-caption">
          <span>CESIUM 3D VIEW</span>
          <span>{{ showEra5 ? `ERA5 ${era5Level} hPa · ${era5StatusLabel}` : 'ERA5 图层已隐藏' }}</span>
        </div>
      </section>

      <aside class="sidebar right-panel">
        <div v-if="selectedTyphoon" class="detail-panel">
          <div class="detail-header">
            <div>
              <span class="eyebrow">SELECTED SYSTEM</span>
              <h2>{{ selectedTyphoon.name || '未命名系统' }}</h2>
              <p>{{ selectedTyphoon.ename || 'Unnamed' }} · {{ selectedTyphoon.tfbh }}</p>
            </div>
            <span class="intensity-chip">{{ currentLabel }}</span>
          </div>

          <div class="detail-tabs">
            <button
              type="button"
              :class="{ active: activeTab === 'overview' }"
              @click="activeTab = 'overview'"
            >
              监测概览
            </button>
            <button
              type="button"
              :class="{ active: activeTab === 'forecast' }"
              @click="activeTab = 'forecast'"
            >
              预测推演
            </button>
            <button
              type="button"
              :class="{ active: activeTab === 'comparison' }"
              @click="activeTab = 'comparison'"
            >
              模型对比
            </button>
            <button
              type="button"
              :class="{ active: activeTab === 'evaluation' }"
              @click="activeTab = 'evaluation'"
            >
              误差评估
            </button>
          </div>

          <template v-if="activeTab === 'overview'">
            <div class="current-readout">
              <div>
                <span class="metric-label">当前时刻</span>
                <strong>{{ formatTime(currentPoint?.time) }}</strong>
              </div>
              <div class="coordinate-readout">
                <span>{{ currentPoint?.lat.toFixed(1) ?? '--' }}°N</span>
                <span>{{ currentPoint?.lng.toFixed(1) ?? '--' }}°E</span>
              </div>
            </div>

            <div class="metric-grid">
              <div class="metric-cell">
                <span>最大风速</span>
                <strong>{{ maxWind || '--' }}</strong>
                <small>m/s</small>
              </div>
              <div class="metric-cell">
                <span>最低气压</span>
                <strong>{{ minPressure ?? '--' }}</strong>
                <small>hPa</small>
              </div>
              <div class="metric-cell">
                <span>轨迹长度</span>
                <strong>{{ trackDistance || '--' }}</strong>
                <small>km</small>
              </div>
              <div class="metric-cell">
                <span>登陆次数</span>
                <strong>{{ landingCount }}</strong>
                <small>records</small>
              </div>
            </div>

            <div class="section-label">
              <span>强度演变</span>
              <span>等级 {{ maxPower || '--' }}</span>
            </div>
            <div class="intensity-strip">
              <span
                v-for="(point, index) in points.slice(0, 32)"
                :key="`${point.time}-${index}`"
                :class="{ current: index === currentIndex }"
                :style="{ height: `${Math.max(12, Math.min(100, (point.power ?? 5) * 5))}%` }"
                :title="`${formatTime(point.time)} · ${point.speed ?? '--'} m/s`"
              ></span>
            </div>
            <section class="environment-panel">
              <div class="section-label">
                <span>ERA5 环境风场</span>
                <span>{{ era5StatusLabel }}</span>
              </div>
              <div v-if="currentEra5" class="environment-grid">
                <div class="environment-cell">
                  <span>{{ era5Level }} hPa 风速</span>
                  <strong>{{ currentWind?.speed_ms.toFixed(1) ?? '--' }}</strong>
                  <small>m/s</small>
                </div>
                <div class="environment-cell">
                  <span>{{ era5Level }} hPa 风向</span>
                  <strong>{{ currentWind ? `${currentWind.direction_deg.toFixed(0)}°` : '--' }}</strong>
                  <small>矢量指向</small>
                </div>
                <div class="environment-cell">
                  <span>500/850 风切变</span>
                  <strong>{{ currentShear?.toFixed(1) ?? '--' }}</strong>
                  <small>m/s</small>
                </div>
                <div class="environment-cell">
                  <span>ERA5 时效</span>
                  <strong>{{ currentEra5.era5_age_hours.toFixed(1) }}</strong>
                  <small>小时</small>
                </div>
              </div>
              <p v-else class="environment-empty">
                {{ era5Error || '当前轨迹点没有匹配的 ERA5 风场样本。' }}
              </p>
            </section>
            <TyphoonCharts :points="points" :current-index="currentIndex" />
          </template>

          <template v-else-if="activeTab === 'forecast'">
            <div class="forecast-workspace">
              <div class="forecast-model-row">
                <div>
                  <span class="metric-label">模型版本</span>
                  <strong>轨迹 CNN baseline</strong>
                  <small class="model-subtitle">ERA5 环境量已接入展示</small>
                </div>
                <span
                  class="forecast-state"
                  :class="{ 'is-ready': health?.model.status === 'ready' }"
                >
                  {{ health?.model.status === 'ready' ? `就绪 · ${health.model.device}` : '不可用' }}
                </span>
              </div>

              <template v-if="prediction">
                <div class="forecast-origin">
                  <span>预测起点</span>
                  <strong>{{ formatTime(forecastOrigin?.time) }}</strong>
                </div>
                <div class="forecast-horizons" role="group" aria-label="预测时效">
                  <button
                    v-for="hour in prediction.horizons_hours"
                    :key="hour"
                    type="button"
                    :aria-pressed="selectedLead === hour"
                    :class="{ active: selectedLead === hour }"
                    @click="selectedLead = hour"
                  >
                    {{ hour }}h
                  </button>
                </div>
                <div v-if="selectedForecastPoint" class="forecast-readout">
                  <div>
                    <span>预测中心</span>
                    <strong>
                      {{ selectedForecastPoint.lat.toFixed(1) }}°N ·
                      {{ selectedForecastPoint.lng.toFixed(1) }}°E
                    </strong>
                  </div>
                  <div>
                    <span>风速</span>
                    <strong>{{ selectedForecastPoint.speed_ms?.toFixed(1) ?? '--' }} m/s</strong>
                  </div>
                  <div>
                    <span>90% 历史误差半径</span>
                    <strong>{{ selectedForecastPoint.location_radius_90_km?.toFixed(0) ?? '--' }} km</strong>
                  </div>
                </div>
                <p class="forecast-quality-note">
                  <template v-if="prediction.uncertainty?.status === 'historical_calibration'">
                    地图圆圈表示同一模型在历史验证集校准的 90% 位置误差范围；不是实时概率保证或灾害风险范围。
                  </template>
                  <template v-else>
                    当前模型没有匹配的历史校准结果，地图不显示概率范围。
                  </template>
                </p>
                <div class="forecast-actions">
                  <button
                    class="primary-button"
                    type="button"
                    :disabled="loadingPrediction || !canPredict"
                    @click="requestPrediction"
                  >
                    {{ loadingPrediction ? '计算中…' : '重新预测' }}
                  </button>
                  <button
                    class="forecast-export"
                    type="button"
                    title="导出预测 JSON"
                    aria-label="导出预测 JSON"
                    @click="downloadPrediction"
                  >
                    ⇩
                  </button>
                </div>
              </template>

              <template v-else>
                <div class="forecast-empty-mark">∿</div>
                <p class="forecast-message">
                  {{ predictionError || predictionAvailabilityMessage || '选择一个可用时间点开始预测。' }}
                </p>
                <button
                  class="primary-button"
                  type="button"
                  :disabled="loadingPrediction || !canPredict"
                  @click="requestPrediction"
                >
                  {{ loadingPrediction ? '计算中…' : '运行 CNN 预测' }}
                </button>
              </template>
            </div>
          </template>

          <template v-else-if="activeTab === 'comparison'">
            <div class="analysis-workspace">
              <div class="analysis-headline">
                <div>
                  <span class="metric-label">固定测试集</span>
                  <strong>{{ experimentSummary?.test_samples ?? '--' }} 个窗口 · {{ experimentSummary?.test_storms ?? '--' }} 个台风</strong>
                </div>
                <span class="forecast-state is-ready">ERA5 500/850</span>
              </div>
              <div class="analysis-horizons" role="group" aria-label="对比时效">
                <button
                  v-for="hour in [6, 12, 18, 24, 30, 36]"
                  :key="hour"
                  type="button"
                  :class="{ active: analysisHorizon === hour }"
                  @click="analysisHorizon = hour"
                >
                  {{ hour }}h
                </button>
              </div>
              <div v-if="experimentSummary?.models.length" class="analysis-table-wrap">
                <table class="analysis-table">
                  <thead>
                    <tr><th>模型</th><th>MAE</th><th>RMSE</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="model in experimentSummary.models" :key="model.key">
                      <td>{{ model.name }}</td>
                      <td>{{ metricAt(model)?.path_mae_km.toFixed(1) ?? '--' }} km</td>
                      <td>{{ metricAt(model)?.path_rmse_km.toFixed(1) ?? '--' }} km</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p v-else class="environment-empty">实验摘要尚未加载。</p>
              <p class="analysis-note">指标来自固定台风分组测试集；当前在线预测仍是轨迹 CNN，ERA5 融合模型用于实验对比。</p>
            </div>
          </template>

          <template v-else>
            <div class="analysis-workspace">
              <div class="analysis-headline">
                <div>
                  <span class="metric-label">{{ analysisHorizon }}h 评估</span>
                  <strong>路径误差与区间覆盖</strong>
                </div>
                <span class="forecast-state is-ready">可追溯实验</span>
              </div>
              <div class="analysis-metric-list">
                <div><span>ERA5 500/850 融合路径 MAE</span><strong>{{ modelMetricText('fusion_500_850', 'path_mae_km') }}</strong></div>
                <div><span>匀速外推路径 MAE</span><strong>{{ modelMetricText('constant_velocity', 'path_mae_km') }}</strong></div>
                <div><span>轨迹 CNN 90% 窗口覆盖率</span><strong>{{ formatPercent(uncertaintyAt()?.location_coverage_90) }}</strong></div>
                <div><span>轨迹 CNN 历史误差半径</span><strong>{{ uncertaintyAt()?.location_radius_90_km?.toFixed(0) ?? '--' }} km</strong></div>
              </div>
              <div class="coverage-bar">
                <span :style="{ width: `${Math.min(100, (uncertaintyAt()?.location_coverage_90 ?? 0) * 100)}%` }"></span>
              </div>
              <p class="analysis-note">目标覆盖率为 90%；区间来自当前在线轨迹 CNN checkpoint 的 MC Dropout + conformal 历史校准，不代表实时灾害风险概率。</p>
            </div>
          </template>

          <div class="detail-footer">
            <span v-if="loadingDetail">详情同步中...</span>
            <span v-else>数据源：本地历史台风数据</span>
            <button class="text-button" type="button" @click="downloadTrack">导出 JSON</button>
          </div>
        </div>
        <div v-else class="detail-empty">
          <span class="empty-orbit">◎</span>
          <h2>等待选择台风</h2>
          <p>{{ dataError || '从左侧档案中选择一个系统开始分析。' }}</p>
        </div>
      </aside>
    </section>

    <footer class="timeline-bar">
      <div class="timeline-control">
        <button class="play-button" type="button" :disabled="!points.length" @click="togglePlay">
          {{ playing ? 'Ⅱ' : '▶' }}
        </button>
        <div class="timeline-copy">
          <span>历史回放</span>
          <small>{{ currentIndex + 1 }} / {{ points.length || 0 }}</small>
        </div>
      </div>
      <div class="timeline-track">
        <input
          v-model.number="currentIndex"
          type="range"
          min="0"
          :max="Math.max(points.length - 1, 0)"
          :style="{ '--progress': `${progress}%` }"
          :disabled="!points.length"
        />
        <div class="timeline-dates">
          <span>{{ formatTime(points[0]?.time) }}</span>
          <span>{{ formatTime(points[points.length - 1]?.time) }}</span>
        </div>
      </div>
      <button class="reset-button" type="button" :disabled="!points.length" @click="resetTimeline">
        重置
      </button>
    </footer>
  </main>
</template>
