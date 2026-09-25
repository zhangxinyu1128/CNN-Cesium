<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import TyphoonGlobe from '@/components/TyphoonGlobe.vue'
import {
  fetchHealth,
  fetchTyphoon,
  fetchTyphoonIndex,
  fetchYears,
  predictTyphoon
} from '@/services/typhoonApi'
import { buildPredictionWindow } from '@/services/predictionWindow'
import type {
  HealthResponse,
  PredictionHistoryPoint,
  PredictionResponse,
  TyphoonDetail,
  TyphoonIndexItem,
  TyphoonPoint
} from '@/types/typhoon'
import '@/styles/index.scss'

const years = ref<number[]>([])
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
const activeTab = ref<'overview' | 'forecast'>('overview')
const currentIndex = ref(0)
const playing = ref(false)
const showTrack = ref(true)
const showForecast = ref(true)
const prediction = ref<PredictionResponse | null>(null)
const predictionInput = ref<PredictionHistoryPoint[] | null>(null)
const predictionError = ref('')
const loadingPrediction = ref(false)
const selectedLead = ref(24)
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
  loadingDetail.value = true
  try {
    selectedTyphoon.value = await fetchTyphoon(id)
    currentIndex.value = 0
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
          <button class="tool-button" type="button" title="回到全局视图" @click="resetTimeline">
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
          <button class="tool-button" type="button" title="导出台风数据" @click="downloadTrack">
            <span>⇩</span>
            <small>导出</small>
          </button>
        </div>
        <TyphoonGlobe
          :points="points"
          :current-index="currentIndex"
          :show-track="showTrack"
          :forecast-start="forecastOrigin"
          :predictions="showForecast ? prediction?.predictions ?? [] : []"
        />
        <div class="map-caption">
          <span>CESIUM 3D VIEW</span>
          <span>经度 95°E — 196°E · 纬度 2°N — 58°N</span>
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
          </template>

          <template v-else>
            <div class="forecast-workspace">
              <div class="forecast-model-row">
                <div>
                  <span class="metric-label">模型版本</span>
                  <strong>{{ health?.model.model_version || '未加载' }}</strong>
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
                </div>
                <p class="forecast-quality-note">
                  固定测试集提示：CNN 路径在 6-18h 尚未优于匀速基线。预测不确定性区间未校准。
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
