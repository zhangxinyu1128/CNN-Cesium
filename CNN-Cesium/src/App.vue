<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import WorkspaceApp from './App.workspace.vue'
import ExperimentComparisonChart from '@/components/ExperimentComparisonChart.vue'
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
  Era5Point,
  Era5Response,
  ExperimentModelSummary,
  ExperimentSummary,
  HealthResponse,
  PredictionResponse,
  TyphoonDetail,
  TyphoonIndexItem
} from '@/types/typhoon'

const showPanel = ref(true)
const showWorkspace = new URLSearchParams(window.location.search).get('workspace') === '1'
const panelMode = ref<'history' | 'forecast' | 'environment' | 'risk'>('history')
const years = ref<number[]>([])
const selectedYear = ref(2025)
const typhoons = ref<TyphoonIndexItem[]>([])
const selectedId = ref('202501')
const selectedTyphoon = ref<TyphoonDetail | null>(null)
const currentIndex = ref(0)
const playing = ref(false)
const health = ref<HealthResponse | null>(null)
const era5 = ref<Era5Response | null>(null)
const era5Error = ref('')
const experimentSummary = ref<ExperimentSummary | null>(null)
const analysisError = ref('')
const analysisHorizon = ref(24)
const comparisonMetric = ref<'mae' | 'rmse'>('mae')
const prediction = ref<PredictionResponse | null>(null)
const selectedLead = ref(24)
const loading = ref(false)
const loadingList = ref(false)
const message = ref('')
const iframeRef = ref<HTMLIFrameElement | null>(null)

type LegacyEntity = Record<string, unknown>
type LegacyReplayPathEntity = LegacyEntity & {
  polyline?: {
    positions?: unknown
    show?: boolean
  }
}
type LegacyColor = {
  withAlpha?: (alpha: number) => unknown
}
type LegacyViewer = {
  entities: {
    add: (options: Record<string, unknown>) => LegacyEntity
    remove: (entity: LegacyEntity) => boolean
  }
  flyTo: (target: LegacyEntity | LegacyEntity[], options?: { duration?: number; offset?: unknown }) => unknown
  camera?: {
    flyTo: (options: {
      destination: unknown
      orientation?: { heading?: number; pitch?: number; roll?: number }
      duration?: number
    }) => unknown
  }
  scene?: { requestRender?: () => void }
}
type LegacyCesium = {
  Cartesian3: {
    fromDegrees: (lng: number, lat: number, height?: number) => unknown
    fromDegreesArray: (values: number[]) => unknown
  }
  Color: { fromCssColorString: (value: string) => LegacyColor }
  HeadingPitchRange: new (heading: number, pitch: number, range: number) => unknown
  Math: { toRadians: (degrees: number) => number }
  PolylineGlowMaterialProperty?: new (options: Record<string, unknown>) => unknown
  PolylineDashMaterialProperty?: new (options: Record<string, unknown>) => unknown
  PolylineArrowMaterialProperty?: new (color: unknown) => unknown
}
type LegacyWindow = Window & { viewer?: LegacyViewer; Cesium?: LegacyCesium }
type LegacyHighlight = {
  entities: LegacyEntity[]
  marker: LegacyEntity
  replayTrail: LegacyReplayPathEntity
  trackPoints: Array<{ lng: number; lat: number }>
  color: string
  cameraTarget: { lng: number; lat: number; height: number }
}
type LegacyPredictionOverlay = {
  entities: LegacyEntity[]
}

const legacyHighlights = new Map<string, LegacyHighlight>()
let legacyPredictionOverlay: LegacyPredictionOverlay | null = null
const highlightColors = ['#ff1493', '#00fff0', '#ffe600', '#76ff03', '#ff6d00']
let legacyObserver: MutationObserver | null = null
let legacySyncTimer: number | undefined
let playTimer: number | undefined
const replayStepMs = 200
let legacyDblClickDocument: Document | null = null
let legacyDblClickWindow: Window | null = null
const legacyDblClickRows = new WeakSet<HTMLTableRowElement>()

const selectedPoint = computed(
  () => prediction.value?.predictions.find((point) => point.lead_hours === selectedLead.value) ?? null
)
const modelReady = computed(() => health.value?.model.status === 'ready')
const windowResult = computed(() =>
  selectedTyphoon.value
    ? buildPredictionWindow(selectedTyphoon.value.points, selectedTyphoon.value.points.length - 1)
    : null
)
const canPredict = computed(() => Boolean(windowResult.value?.ok))
const currentPoint = computed(() => selectedTyphoon.value?.points[currentIndex.value] ?? null)
const replayProgress = computed(() => {
  const length = selectedTyphoon.value?.points.length ?? 0
  return length > 1 ? (currentIndex.value / (length - 1)) * 100 : 0
})
const selectedEra5Point = computed<Era5Point | null>(() => {
  const point = currentPoint.value
  if (!point || !era5.value?.points.length) return null
  const target = canonicalTime(point.time)
  return era5.value.points.find((item) => canonicalTime(item.time) === target) ?? null
})
const currentWind500 = computed(() => selectedEra5Point.value?.levels['500'] ?? null)
const currentWind850 = computed(() => selectedEra5Point.value?.levels['850'] ?? null)
const currentShear = computed(() => selectedEra5Point.value?.shear_500_850_ms ?? null)
const era5Status = computed(() => {
  if (era5Error.value) return '读取失败'
  if (!era5.value) return '同步中'
  return selectedEra5Point.value && (currentWind500.value || currentWind850.value) ? '已同步' : '未匹配'
})

function canonicalTime(value?: string | null) {
  if (!value) return ''
  const match = value
    .replace('Z', '')
    .replace(/\//g, '-')
    .match(/^(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?/)
  if (!match) return value.slice(0, 19)
  const [, year, month, day, hour, minute, second = '00'] = match
  return `${year}-${month.padStart(2, '0')}-${day.padStart(2, '0')}T${hour.padStart(2, '0')}:${minute}:${second}`
}

function metricAt(model: ExperimentModelSummary, lead = analysisHorizon.value) {
  return model.by_horizon.find((item) => item.lead_hours === lead) ?? model.by_horizon[0]
}

function formatPercent(value?: number | null) {
  return typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '--'
}

function uncertaintyAt(lead = analysisHorizon.value) {
  return experimentSummary.value?.uncertainty.by_horizon?.find((item) => item.lead_hours === lead)
}

function windArrowStyle(direction?: number | null) {
  return typeof direction === 'number'
    ? { transform: `rotate(${direction}deg)` }
    : undefined
}

function metricValue(model: ExperimentModelSummary) {
  const metric = metricAt(model)
  const value = comparisonMetric.value === 'rmse' ? metric?.path_rmse_km : metric?.path_mae_km
  const deviation = comparisonMetric.value === 'rmse' ? metric?.path_rmse_km_std : metric?.path_mae_km_std
  if (typeof value !== 'number') return '--'
  return typeof deviation === 'number' ? `${value.toFixed(1)} ± ${deviation.toFixed(1)} km` : `${value.toFixed(1)} km`
}

function legacyFrame(): LegacyWindow | null {
  return iframeRef.value?.contentWindow as LegacyWindow | null
}

function removeLegacyHighlight(id: string) {
  const frame = legacyFrame()
  const highlight = legacyHighlights.get(id)
  if (!frame?.viewer || !highlight) return
  highlight.entities.forEach((entity) => frame.viewer?.entities.remove(entity))
  legacyHighlights.delete(id)
}

function flyToLegacyHighlight(highlight: LegacyHighlight) {
  const frame = legacyFrame()
  const Cesium = frame?.Cesium
  const viewer = frame?.viewer
  if (!Cesium || !viewer) return
  void viewer.flyTo([highlight.entities[0], highlight.marker], {
    duration: 1.1,
    offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-68), 0)
  })
}

function addLegacyHighlight(detail: TyphoonDetail, flyTo = false) {
  const frame = legacyFrame()
  const Cesium = frame?.Cesium
  const viewer = frame?.viewer
  const points = detail.points.filter(
    (point) => Number.isFinite(point.lng) && Number.isFinite(point.lat)
  )
  if (!Cesium || !viewer || points.length < 2) return

  removeLegacyHighlight(detail.tfbh)
  const color = detail.tfbh === selectedId.value
    ? '#b8c5cb'
    : highlightColors[legacyHighlights.size % highlightColors.length]
  const lineColor = Cesium.Color.fromCssColorString(color)
  const replayColor = Cesium.Color.fromCssColorString('#67e8d0')
  const white = Cesium.Color.fromCssColorString('#ffffff')
  const positions = points.flatMap((point) => [point.lng, point.lat])
  const material = Cesium.PolylineGlowMaterialProperty
    ? new Cesium.PolylineGlowMaterialProperty({ glowPower: 0.25, taperPower: 0.8, color: lineColor })
    : lineColor
  const line = viewer.entities.add({
    id: `MainHighlightLine-${detail.tfbh}`,
    name: `${detail.name || detail.ename || detail.tfbh} 选中轨迹`,
    polyline: {
      positions: Cesium.Cartesian3.fromDegreesArray(positions),
      width: 5,
      clampToGround: true,
      material
    }
  })
  const last = points[points.length - 1]
  const replayPoint = detail.tfbh === selectedId.value
    ? points[currentIndex.value] ?? last
    : last
  const marker = viewer.entities.add({
    id: `MainHighlightMarker-${detail.tfbh}`,
    name: `${detail.name || detail.ename || detail.tfbh} 当前位置`,
    position: Cesium.Cartesian3.fromDegrees(replayPoint.lng, replayPoint.lat, 12000),
    point: { pixelSize: 15, color: lineColor, outlineColor: white, outlineWidth: 3 }
  })
  const replayTrail = viewer.entities.add({
    id: `MainReplayTrail-${detail.tfbh}`,
    name: `${detail.name || detail.ename || detail.tfbh} 已回放轨迹`,
    polyline: {
      positions: Cesium.Cartesian3.fromDegreesArray(positions),
      width: 7,
      clampToGround: true,
      material: replayColor,
      show: detail.tfbh === selectedId.value && currentIndex.value > 0
    }
  }) as LegacyReplayPathEntity
  const minLng = Math.min(...points.map((point) => point.lng))
  const maxLng = Math.max(...points.map((point) => point.lng))
  const minLat = Math.min(...points.map((point) => point.lat))
  const maxLat = Math.max(...points.map((point) => point.lat))
  const centerLng = (minLng + maxLng) / 2
  const centerLat = (minLat + maxLat) / 2
  const span = Math.max(maxLat - minLat, (maxLng - minLng) * Math.cos(centerLat * Math.PI / 180), 2)
  const height = Math.min(Math.max(span * 110000, 900000), 5000000)
  const highlight = {
    entities: [line, replayTrail, marker],
    marker,
    replayTrail,
    trackPoints: points,
    color,
    cameraTarget: { lng: centerLng, lat: centerLat, height }
  }
  legacyHighlights.set(detail.tfbh, highlight)
  if (flyTo) flyToLegacyHighlight(highlight)
  viewer.scene?.requestRender?.()
}

function updateLegacyMarker() {
  const point = currentPoint.value
  const frame = legacyFrame()
  const Cesium = frame?.Cesium
  const highlight = legacyHighlights.get(selectedId.value)
  if (!point || !Cesium || !highlight || !frame?.viewer) return
  highlight.marker.position = Cesium.Cartesian3.fromDegrees(point.lng, point.lat, 12000)
  const trail = highlight.replayTrail.polyline
  if (trail) {
    const trailEnd = Math.min(currentIndex.value, highlight.trackPoints.length - 1)
    const visiblePoints = trailEnd > 0
      ? highlight.trackPoints.slice(0, trailEnd + 1)
      : highlight.trackPoints.slice(0, Math.min(2, highlight.trackPoints.length))
    trail.positions = Cesium.Cartesian3.fromDegreesArray(
      visiblePoints.flatMap((item) => [item.lng, item.lat])
    )
    trail.show = trailEnd > 0
  }
  frame.viewer.scene?.requestRender?.()
}

function clearLegacyPrediction() {
  const viewer = legacyFrame()?.viewer
  if (viewer && legacyPredictionOverlay) {
    legacyPredictionOverlay.entities.forEach((entity) => viewer.entities.remove(entity))
  }
  legacyPredictionOverlay = null
}

function renderLegacyPrediction() {
  clearLegacyPrediction()
  const frame = legacyFrame()
  const Cesium = frame?.Cesium
  const viewer = frame?.viewer
  const history = windowResult.value?.history ?? []
  const start = history[history.length - 1]
  const forecastPoints = prediction.value?.predictions.filter(
    (point) => Number.isFinite(point.lng) && Number.isFinite(point.lat)
  ) ?? []
  if (!Cesium || !viewer || !start || !forecastPoints.length) return

  const entities: LegacyEntity[] = []
  const lineColor = Cesium.Color.fromCssColorString('#67e8d0')
  const errorColor = Cesium.Color.fromCssColorString('#f7c873')
  const originColor = Cesium.Color.fromCssColorString('#f7c873')
  const route = [start, ...forecastPoints]
  const routePositions = Cesium.Cartesian3.fromDegreesArray(
    route.flatMap((point) => [point.lng, point.lat])
  )
  const dashedMaterial = Cesium.PolylineDashMaterialProperty
    ? new Cesium.PolylineDashMaterialProperty({ color: lineColor, dashLength: 14 })
    : lineColor

  entities.push(viewer.entities.add({
    id: `MainPredictionLine-${selectedId.value}`,
    name: `${selectedId.value} CNN 预测路径`,
    polyline: {
      positions: routePositions,
      width: 4,
      clampToGround: true,
      material: dashedMaterial
    }
  }))

  for (let index = 1; index < route.length; index += 1) {
    const from = route[index - 1]
    const to = route[index]
    const arrowMaterial = Cesium.PolylineArrowMaterialProperty
      ? new Cesium.PolylineArrowMaterialProperty(lineColor)
      : lineColor
    entities.push(viewer.entities.add({
      id: `MainPredictionArrow-${selectedId.value}-${index}`,
      name: `CNN 预测方向 +${forecastPoints[index - 1].lead_hours} 小时`,
      polyline: {
        positions: Cesium.Cartesian3.fromDegreesArray([from.lng, from.lat, to.lng, to.lat]),
        width: 7,
        clampToGround: true,
        material: arrowMaterial
      }
    }))
  }

  entities.push(viewer.entities.add({
    id: `MainPredictionOrigin-${selectedId.value}`,
    name: `CNN 预测起点 ${start.time}`,
    position: Cesium.Cartesian3.fromDegrees(start.lng, start.lat, 18000),
    point: { pixelSize: 10, color: originColor, outlineColor: lineColor, outlineWidth: 2 }
  }))
  forecastPoints.forEach((point) => {
    if (typeof point.location_radius_90_km === 'number' && point.location_radius_90_km > 0) {
      entities.push(viewer.entities.add({
        id: `MainPredictionUncertainty-${selectedId.value}-${point.lead_hours}`,
        name: `+${point.lead_hours} 小时 · 90% 历史校准位置范围 · 半径 ${Math.round(point.location_radius_90_km)} km`,
        position: Cesium.Cartesian3.fromDegrees(point.lng, point.lat, 18000),
        ellipse: {
          semiMajorAxis: point.location_radius_90_km * 1000,
          semiMinorAxis: point.location_radius_90_km * 1000,
          material: errorColor.withAlpha(0.10),
          outline: true,
          outlineColor: errorColor.withAlpha(0.72),
          outlineWidth: 1,
          height: 18000
        }
      }))
    }
    entities.push(viewer.entities.add({
      id: `MainPredictionPoint-${selectedId.value}-${point.lead_hours}`,
      name: `CNN 预测 +${point.lead_hours} 小时`,
      position: Cesium.Cartesian3.fromDegrees(point.lng, point.lat, 18000),
      point: { pixelSize: 8, color: lineColor, outlineColor: originColor, outlineWidth: 2 }
    }))
  })

  legacyPredictionOverlay = { entities }
  viewer.scene?.requestRender?.()
  void viewer.flyTo(entities, {
    duration: 0.9,
    offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-68), 0)
  })
}

function requestLegacyPredictionRender(attempt = 0) {
  renderLegacyPrediction()
  if (legacyPredictionOverlay || attempt >= 12 || !prediction.value) return
  window.setTimeout(() => requestLegacyPredictionRender(attempt + 1), 250)
}

function checkedLegacyIds() {
  const doc = iframeRef.value?.contentDocument
  if (!doc) return []
  return Array.from(doc.querySelectorAll('tr')).flatMap((row) => {
    const checkbox = row.querySelector<HTMLInputElement>('input[type="checkbox"]')
    const id = row.textContent?.match(/\b\d{4,8}\b/)?.[0]
    return checkbox?.checked && id ? [id] : []
  })
}

function handleLegacyTyphoonDblClick(event: MouseEvent) {
  const doc = iframeRef.value?.contentDocument
  const target = event.target as Node | null
  if (!doc || !target || !doc.contains(target)) return

  const element = target.nodeType === 1
    ? target as Element
    : target.parentElement
  const row = element?.closest('tr')
  if (!row || !row.querySelector('input[type="checkbox"]')) return

  const candidates = row.textContent?.match(/\b\d{4,8}\b/g) ?? []
  const id = candidates.find((candidate) => typhoons.value.some((item) => item.tfbh === candidate))
    ?? candidates[0]
  if (!id) return

  // The legacy table has its own row-dblclick camera animation. Capture this
  // event first so the main page and the legacy table share one camera view.
  event.preventDefault()
  event.stopImmediatePropagation()
  selectedId.value = id
  void loadSelected(id)
}

function bindLegacyTyphoonRows(doc: Document) {
  doc.querySelectorAll<HTMLTableRowElement>('tr').forEach((row) => {
    if (legacyDblClickRows.has(row) || !row.querySelector('input[type="checkbox"]')) return
    legacyDblClickRows.add(row)
    row.addEventListener('dblclick', handleLegacyTyphoonDblClick, true)
  })
}

async function syncLegacyHighlights(flyToId = '') {
  // Keep the active replay track independent of the legacy table's checkbox
  // timing. The checkbox controls extra comparison tracks, but must not be
  // allowed to remove the track driven by the main-page timeline.
  const ids = new Set([
    ...checkedLegacyIds(),
    ...(selectedTyphoon.value ? [selectedId.value] : [])
  ])
  for (const id of ids) {
    if (legacyHighlights.has(id)) {
      if (id === flyToId) {
        const highlight = legacyHighlights.get(id)
        if (highlight) flyToLegacyHighlight(highlight)
      }
      continue
    }
    try {
      const detail = id === selectedId.value && selectedTyphoon.value
        ? selectedTyphoon.value
        : await fetchTyphoon(id)
      addLegacyHighlight(detail, id === flyToId)
    } catch {
      // The original page can still display its own track if a detail request is unavailable.
    }
  }
  for (const id of [...legacyHighlights.keys()]) {
    if (!ids.has(id)) removeLegacyHighlight(id)
  }
}

function requestLegacyHighlightSync(flyToId = '') {
  if (legacySyncTimer) window.clearTimeout(legacySyncTimer)
  legacySyncTimer = window.setTimeout(() => {
    void syncLegacyHighlights(flyToId)
  }, 120)
}

function legacySelect(id: string, attempt = 0) {
  const doc = iframeRef.value?.contentDocument
  if (!doc) return
  const row = Array.from(doc.querySelectorAll('tr')).find((item) => item.textContent?.includes(id))
  const checkbox = row?.querySelector<HTMLInputElement>('input[type="checkbox"]')
  if (!checkbox) {
    if (attempt < 20) window.setTimeout(() => legacySelect(id, attempt + 1), 250)
    return
  }
  if (!checkbox.checked) checkbox.click()
  requestLegacyHighlightSync(id)
}

function syncLegacyYear(year: number, attempt = 0) {
  const doc = iframeRef.value?.contentDocument
  const select = doc?.querySelector<HTMLElement>('.el-select')
  if (!doc || !select) {
    if (attempt < 20) window.setTimeout(() => syncLegacyYear(year, attempt + 1), 250)
    return
  }
  const label = `${year}年`
  const current = select.querySelector('.el-select__selected-item')?.textContent?.trim()
  if (current === label) {
    legacySelect(selectedId.value)
    return
  }
  ;(select.querySelector<HTMLElement>('.el-select__wrapper') || select).click()
  window.setTimeout(() => {
    const option = Array.from(doc.querySelectorAll<HTMLElement>('.el-select-dropdown__item'))
      .find((item) => item.textContent?.trim() === label)
    if (option) {
      option.click()
      window.setTimeout(() => legacySelect(selectedId.value), 700)
    } else if (attempt < 20) {
      syncLegacyYear(year, attempt + 1)
    }
  }, 120)
}

function hideLegacyWorkspaceLink() {
  const doc = iframeRef.value?.contentDocument
  if (!doc) return
  let style = doc.getElementById('main-screen-overrides') as HTMLStyleElement | null
  if (!style) {
    style = doc.createElement('style')
    style.id = 'main-screen-overrides'
    doc.head?.appendChild(style)
  }
  style.textContent = '.layout .layout-header { display: none !important; }'
  doc.querySelectorAll('a[href*="workspace"]').forEach((link) => {
    ;(link as HTMLElement).style.display = 'none'
  })
  legacyDblClickDocument?.removeEventListener('dblclick', handleLegacyTyphoonDblClick, true)
  legacyDblClickWindow?.removeEventListener('dblclick', handleLegacyTyphoonDblClick, true)
  legacyDblClickDocument = doc
  legacyDblClickWindow = doc.defaultView
  doc.addEventListener('dblclick', handleLegacyTyphoonDblClick, true)
  legacyDblClickWindow?.addEventListener('dblclick', handleLegacyTyphoonDblClick, true)
  bindLegacyTyphoonRows(doc)
  legacyObserver?.disconnect()
  if (doc.body) {
    legacyObserver = new MutationObserver(() => {
      bindLegacyTyphoonRows(doc)
      requestLegacyHighlightSync()
    })
    legacyObserver.observe(doc.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['class', 'checked', 'aria-checked']
    })
  }
  syncLegacyYear(selectedYear.value)
  window.setTimeout(() => requestLegacyHighlightSync(selectedId.value), 900)
  window.setTimeout(() => {
    requestLegacyPredictionRender()
  }, 1200)
}

async function loadList() {
  loadingList.value = true
  try {
    typhoons.value = await fetchTyphoonIndex(selectedYear.value)
    if (!typhoons.value.some((item) => item.tfbh === selectedId.value)) {
      selectedId.value = typhoons.value[0]?.tfbh ?? ''
    }
    syncLegacyYear(selectedYear.value)
  } catch (error) {
    message.value = error instanceof Error ? error.message : '台风列表加载失败'
  } finally {
    loadingList.value = false
  }
}

async function loadSelected(id = selectedId.value) {
  if (!id) return
  loading.value = true
  message.value = ''
  prediction.value = null
  clearLegacyPrediction()
  stopReplay()
  currentIndex.value = 0
  era5.value = null
  era5Error.value = ''
  try {
    selectedTyphoon.value = await fetchTyphoon(id)
    try {
      era5.value = await fetchTyphoonEra5(id)
    } catch (error) {
      era5Error.value = error instanceof Error ? error.message : 'ERA5 风场加载失败'
    }
    window.setTimeout(() => legacySelect(id), 250)
    window.setTimeout(() => {
      if (selectedTyphoon.value?.tfbh === id) {
        addLegacyHighlight(selectedTyphoon.value, true)
      }
    }, 650)
  } catch (error) {
    selectedTyphoon.value = null
    message.value = error instanceof Error ? error.message : '台风详情加载失败'
  } finally {
    loading.value = false
  }
}

async function loadAnalysisSummary() {
  try {
    experimentSummary.value = await fetchExperimentSummary()
    analysisError.value = ''
  } catch (error) {
    analysisError.value = error instanceof Error ? error.message : '实验摘要加载失败'
  }
}

function stopReplay() {
  playing.value = false
  if (playTimer) {
    window.clearInterval(playTimer)
    playTimer = undefined
  }
}

function toggleReplay() {
  const length = selectedTyphoon.value?.points.length ?? 0
  if (!length) return
  if (playing.value) {
    stopReplay()
    return
  }
  playing.value = true
  playTimer = window.setInterval(() => {
    currentIndex.value = currentIndex.value >= length - 1 ? 0 : currentIndex.value + 1
  }, replayStepMs)
}

function resetTimeline() {
  stopReplay()
  currentIndex.value = 0
  updateLegacyMarker()
}

async function runPrediction() {
  if (!selectedTyphoon.value || !windowResult.value?.ok) {
    message.value = windowResult.value?.reason || '当前台风没有足够的连续历史观测。'
    return
  }
  if (!modelReady.value) {
    message.value = health.value?.model.reason || '模型不可用，请先安装 PyTorch。'
    return
  }
  loading.value = true
  message.value = ''
  try {
    prediction.value = await predictTyphoon(selectedId.value, windowResult.value.history)
    selectedLead.value = prediction.value.horizons_hours.includes(selectedLead.value)
      ? selectedLead.value
      : prediction.value.horizons_hours[0]
    requestLegacyPredictionRender()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '预测请求失败'
  } finally {
    loading.value = false
  }
}

function downloadTrack() {
  if (!selectedTyphoon.value) return
  const defaultName = `${selectedTyphoon.value.tfbh}-track`
  const inputName = window.prompt('请输入导出文件名（不含扩展名）', defaultName)
  if (inputName === null) return

  const safeName = inputName
    .trim()
    .replace(new RegExp(`[<>:"/\\\\|?*${String.fromCharCode(0)}-${String.fromCharCode(31)}]`, 'g'), '-')
    .replace(/[. ]+$/, '')
  if (!safeName) {
    message.value = '文件名不能为空'
    return
  }

  const exportPayload = {
    exported_at: new Date().toISOString(),
    mode: panelMode.value,
    typhoon: selectedTyphoon.value,
    replay: {
      current_index: currentIndex.value,
      current_point: currentPoint.value,
      is_playing: playing.value,
    },
    prediction: prediction.value,
    era5: era5.value,
    experiment_summary: experimentSummary.value,
  }
  const blob = new Blob([JSON.stringify(exportPayload, null, 2)], {
    type: 'application/json'
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${safeName.replace(/\.json$/i, '')}.json`
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

function openPanel(mode: 'history' | 'forecast' | 'environment' | 'risk') {
  panelMode.value = mode
  showPanel.value = true
}

function resetGlobal() {
  const frame = iframeRef.value?.contentWindow as (Window & {
    viewer?: { camera?: { flyHome?: (duration?: number) => void }; scene?: { requestRender?: () => void } }
  }) | null
  if (frame?.viewer?.camera?.flyHome) {
    frame.viewer.camera.flyHome(1.2)
    frame.viewer.scene?.requestRender?.()
    return
  }

  const doc = iframeRef.value?.contentDocument
  const button = Array.from(doc?.querySelectorAll('button, [role="button"]') ?? []).find(
    (item) => item.textContent?.includes('全局')
  ) as HTMLElement | undefined
  button?.click()
}

watch(currentIndex, () => {
  updateLegacyMarker()
})

watch(prediction, () => requestLegacyPredictionRender(), { deep: true })

onUnmounted(() => {
  stopReplay()
  clearLegacyPrediction()
  legacyDblClickDocument?.removeEventListener('dblclick', handleLegacyTyphoonDblClick, true)
  legacyDblClickWindow?.removeEventListener('dblclick', handleLegacyTyphoonDblClick, true)
  legacyObserver?.disconnect()
  if (legacySyncTimer) window.clearTimeout(legacySyncTimer)
})

onMounted(async () => {
  health.value = await fetchHealth().catch(() => null)
  years.value = await fetchYears().catch(() => [])
  if (!years.value.includes(selectedYear.value)) selectedYear.value = years.value[0] ?? 2024
  await Promise.all([loadList(), loadAnalysisSummary()])
  await loadSelected()
})
</script>

<template>
  <WorkspaceApp v-if="showWorkspace" />
  <main v-else class="screen">
    <iframe
      ref="iframeRef"
      class="dashboard"
      src="/legacy/index.html"
      title="历年台风展示监控平台"
      @load="hideLegacyWorkspaceLink"
    />

    <header class="screen-header" aria-label="平台导航">
      <div class="screen-brand">
        <span class="screen-brand-kicker">TYPHOON INTELLIGENCE</span>
        <h1>历年台风检测平台</h1>
        <span class="screen-brand-subtitle">轨迹回放 · CNN 预测 · ERA5 风场</span>
      </div>

      <nav class="top-actions" aria-label="功能模块">
        <button type="button" title="回到大屏全局视角" @click="resetGlobal">⌂ <span>全局视角</span></button>
        <button type="button" :class="{ active: showPanel && panelMode === 'history' }" title="打开历史路径面板" @click="openPanel('history')">⌁ <span>历史路径</span></button>
        <button type="button" :class="{ active: showPanel && panelMode === 'forecast' }" title="打开模型预测面板" @click="openPanel('forecast')">◈ <span>模型预测</span></button>
        <button type="button" :class="{ active: showPanel && panelMode === 'environment' }" title="打开 ERA5 环境场面板" @click="openPanel('environment')">↗ <span>环境风场</span></button>
        <button type="button" :class="{ active: showPanel && panelMode === 'risk' }" title="打开风险研判面板" @click="openPanel('risk')">△ <span>风险研判</span></button>
        <button type="button" title="导出当前台风分析 JSON" :disabled="!selectedTyphoon" @click="downloadTrack">⇩ <span>导出</span></button>
      </nav>
    </header>

    <section v-if="showPanel" class="forecast-panel analysis-panel">
      <div class="panel-title">
        <div>
          <span class="kicker">TYphoon INTELLIGENCE</span>
          <h2>{{ panelMode === 'history' ? '历史路径' : panelMode === 'forecast' ? '模型预测' : panelMode === 'environment' ? 'ERA5 环境风场' : '风险研判' }}</h2>
          <p class="panel-context">{{ selectedId }} · {{ selectedTyphoon?.name || selectedTyphoon?.ename || '未选择台风' }}</p>
        </div>
        <button class="close-button" type="button" title="隐藏分析面板" @click="showPanel = false">×</button>
      </div>

      <div class="panel-mode-tabs" role="tablist" aria-label="分析模式">
        <button type="button" :class="{ active: panelMode === 'history' }" role="tab" :aria-selected="panelMode === 'history'" @click="openPanel('history')">历史</button>
        <button type="button" :class="{ active: panelMode === 'forecast' }" role="tab" :aria-selected="panelMode === 'forecast'" @click="openPanel('forecast')">预测</button>
        <button type="button" :class="{ active: panelMode === 'environment' }" role="tab" :aria-selected="panelMode === 'environment'" @click="openPanel('environment')">环境</button>
        <button type="button" :class="{ active: panelMode === 'risk' }" role="tab" :aria-selected="panelMode === 'risk'" @click="openPanel('risk')">风险</button>
      </div>

      <div class="selection-block">
        <label class="field-label" for="legacy-year">年份</label>
        <select id="legacy-year" v-model="selectedYear" :disabled="loadingList" @change="loadList">
          <option v-for="year in years" :key="year" :value="year">{{ year }} 年</option>
        </select>

        <label class="field-label" for="legacy-typhoon">台风</label>
        <select id="legacy-typhoon" v-model="selectedId" :disabled="loading || !typhoons.length" @change="loadSelected()">
          <option v-for="item in typhoons" :key="item.tfbh" :value="item.tfbh">
            {{ item.tfbh }} · {{ item.name || item.ename || '未命名' }}
          </option>
        </select>
      </div>

      <template v-if="panelMode === 'history'">
        <div class="history-overview">
          <div><span>当前观测</span><strong>{{ currentPoint?.time?.replace('T', ' ') ?? '--' }}</strong></div>
          <div><span>位置</span><strong>{{ currentPoint?.lat.toFixed(1) ?? '--' }}°N · {{ currentPoint?.lng.toFixed(1) ?? '--' }}°E</strong></div>
          <div><span>轨迹点</span><strong>{{ selectedTyphoon?.points.length ?? 0 }}</strong></div>
          <div><span>回放状态</span><strong>{{ playing ? '播放中' : '已暂停' }}</strong></div>
        </div>
        <TyphoonCharts v-if="selectedTyphoon" :points="selectedTyphoon.points" :current-index="currentIndex" />
        <div class="map-legend" aria-label="地图图例">
          <span><i class="legend-swatch best-track"></i>灰色：观测最佳路径</span>
          <span><i class="legend-swatch replay-track"></i>青色：回放已观测段</span>
          <span><i class="legend-swatch forecast-track"></i>青色虚线：模型预测路径</span>
          <span v-if="prediction"><i class="legend-swatch error-range"></i>橙色：历史校准误差范围</span>
        </div>
        <small class="panel-note">真实观测点不做视觉插值；青色已观测段仅表示回放进度，图表只绘制数据中存在的字段。</small>
      </template>

      <template v-else-if="panelMode === 'forecast'">
        <div class="model-line">
          <span class="status-dot" :class="{ ready: modelReady }"></span>
          <span>{{ health ? (modelReady ? '轨迹 CNN 就绪 · ' + (health.model.device ?? '') : '模型不可用') : '服务状态检查中' }}</span>
        </div>

        <div class="selection-info">
          <strong>{{ selectedTyphoon?.name || '选择一个台风' }}</strong>
          <span>{{ selectedTyphoon?.ename || selectedId }}</span>
        </div>

        <div v-if="prediction" class="horizon-group" aria-label="预测时效">
          <button v-for="hour in prediction.horizons_hours" :key="hour" type="button" :class="{ active: selectedLead === hour }" @click="selectedLead = hour">
            {{ hour }}h
          </button>
        </div>

        <div v-if="selectedPoint" class="prediction-readout">
          <div><span>预测位置</span><strong>{{ selectedPoint.lat.toFixed(1) }}°N · {{ selectedPoint.lng.toFixed(1) }}°E</strong></div>
          <div><span>预计风速</span><strong>{{ selectedPoint.speed_ms?.toFixed(1) ?? '--' }} m/s</strong></div>
          <div><span>90% 历史误差半径</span><strong>{{ selectedPoint.location_radius_90_km?.toFixed(0) ?? '--' }} km</strong></div>
          <div><span>预测状态</span><strong>历史回放 / 模拟预测</strong></div>
        </div>

        <div class="prediction-meta">
          <div><span>模型版本</span><strong>{{ prediction?.model_version || health?.model.model_version || '--' }}</strong></div>
          <div><span>ERA5 匹配</span><strong>{{ era5Status }} · {{ era5?.matched_points ?? 0 }} 点</strong></div>
          <div><span>在线输入</span><strong>轨迹特征</strong></div>
          <div><span>误差单位</span><strong>球面距离 / km</strong></div>
        </div>

        <p v-if="message" class="message">{{ message }}</p>
        <p v-else-if="!windowResult?.ok && selectedTyphoon" class="message">{{ windowResult?.reason }}</p>

        <button class="run-button" type="button" :disabled="loading || !selectedTyphoon || !canPredict || !modelReady" @click="runPrediction">
          {{ loading ? '计算中...' : prediction ? '重新预测' : '运行 CNN 预测' }}
        </button>
        <div class="section-heading"><span>模型对比实验</span><small>{{ experimentSummary?.test_samples ?? '--' }} 个 ERA5 配对测试窗口</small></div>
        <div class="metric-switch" role="group" aria-label="对比指标">
          <button type="button" :class="{ active: comparisonMetric === 'mae' }" @click="comparisonMetric = 'mae'">MAE</button>
          <button type="button" :class="{ active: comparisonMetric === 'rmse' }" @click="comparisonMetric = 'rmse'">RMSE</button>
        </div>
        <div class="analysis-horizons" role="group" aria-label="对比时效">
          <button v-for="hour in [6, 12, 18, 24, 30, 36]" :key="hour" type="button" :class="{ active: analysisHorizon === hour }" @click="analysisHorizon = hour">{{ hour }}h</button>
        </div>
        <ExperimentComparisonChart v-if="experimentSummary?.models.length" :models="experimentSummary.models" :horizon="analysisHorizon" :metric="comparisonMetric" />
        <p v-else class="message">{{ analysisError || '实验摘要加载中...' }}</p>
        <div v-if="experimentSummary?.models.length" class="comparison-snapshot">
          <div v-for="model in experimentSummary.models" :key="model.key">
            <span>{{ model.name }}<small>{{ model.seed_count ?? 0 }} 个 seed</small></span>
            <strong>{{ metricValue(model) }}</strong>
          </div>
        </div>
        <div v-if="experimentSummary?.official_forecast" class="audit-status" :class="{ unavailable: experimentSummary.official_forecast.status !== 'ready' }">
          <span>官方预报对比</span>
          <strong>{{ experimentSummary.official_forecast.status === 'ready' ? '已纳入可比样本' : '已审计，暂不纳入图表' }}</strong>
          <small>{{ experimentSummary.official_forecast.reason || '官方预报对比结果可追溯。' }}</small>
        </div>
        <small class="panel-note">图表只显示已有的 6/12/18/24/30/36 小时 MAE 或 RMSE；当前没有 ADE/FDE、强度分组、转向/登陆分组和独立物理约束结果，因此不展示对应栏目。</small>
      </template>

      <template v-else-if="panelMode === 'environment'">
        <div class="model-line">
          <span class="status-dot" :class="{ ready: era5Status === '已同步' }"></span>
          <span>{{ era5Status }} · {{ era5?.matched_points ?? 0 }} 个匹配样本</span>
        </div>
        <div v-if="selectedEra5Point" class="environment-grid environment-level-grid">
          <div v-if="currentWind500"><span>500 hPa 风速</span><strong>{{ currentWind500.speed_ms.toFixed(1) }}</strong><small>m/s · 风向 {{ currentWind500.direction_deg.toFixed(0) }}°</small></div>
          <div v-if="currentWind850"><span>850 hPa 风速</span><strong>{{ currentWind850.speed_ms.toFixed(1) }}</strong><small>m/s · 风向 {{ currentWind850.direction_deg.toFixed(0) }}°</small></div>
          <div v-if="currentShear !== null"><span>500/850 垂直风切变</span><strong>{{ currentShear.toFixed(1) }}</strong><small>m/s · u/v 差的模</small></div>
          <div><span>ERA5 时效</span><strong>{{ selectedEra5Point.era5_age_hours.toFixed(1) }}</strong><small>小时</small></div>
        </div>
        <div v-if="selectedEra5Point && (currentWind500 || currentWind850)" class="wind-vector-list">
          <div v-if="currentWind500" class="wind-vector-readout">
            <span class="wind-arrow" :style="windArrowStyle(currentWind500.direction_deg)">➤</span>
            <div><strong>500 hPa 风向矢量</strong><small>{{ currentWind500.u.toFixed(1) }} / {{ currentWind500.v.toFixed(1) }} m/s</small></div>
          </div>
          <div v-if="currentWind850" class="wind-vector-readout">
            <span class="wind-arrow" :style="windArrowStyle(currentWind850.direction_deg)">➤</span>
            <div><strong>850 hPa 风向矢量</strong><small>{{ currentWind850.u.toFixed(1) }} / {{ currentWind850.v.toFixed(1) }} m/s</small></div>
          </div>
        </div>
        <div v-if="selectedEra5Point" class="source-meta">
          <div><span>观测时间</span><strong>{{ selectedEra5Point.time.replace('T', ' ') }}</strong></div>
          <div><span>ERA5 数据时间</span><strong>{{ selectedEra5Point.era5_time_utc.replace('T', ' ') }}</strong></div>
          <div><span>数据来源</span><strong>Copernicus ERA5</strong></div>
          <div><span>气压层</span><strong>500 / 850 hPa</strong></div>
          <div v-if="experimentSummary?.era5"><span>ERA5 实际覆盖</span><strong>{{ experimentSummary.era5.years?.[0] ?? '--' }}–{{ experimentSummary.era5.years?.[experimentSummary.era5.years.length - 1] ?? '--' }}（{{ experimentSummary.era5.years?.length ?? 0 }} 年）</strong></div>
          <div v-if="experimentSummary?.era5"><span>轨迹点匹配率</span><strong>{{ formatPercent(experimentSummary.era5.paired_point_fraction) }}</strong></div>
        </div>
        <p v-else class="message">{{ era5Error || '当前台风暂时没有 ERA5 中心采样结果。' }}</p>
        <small class="panel-note">当前数据只有沿台风中心轨迹匹配的 500/850 hPa u/v 样本，因此不显示没有数据支撑的完整区域风速图、湿度、海温、海平面气压或环境场剖面。</small>
      </template>

      <template v-else>
        <div class="analysis-headline">
          <div><span>{{ analysisHorizon }}h 历史校准范围</span><strong>在线轨迹 CNN 的位置误差</strong></div>
          <span>{{ experimentSummary?.uncertainty.model_version || '--' }}</span>
        </div>
        <div class="analysis-horizons" role="group" aria-label="评估时效">
          <button v-for="hour in [6, 12, 18, 24, 30, 36]" :key="hour" type="button" :class="{ active: analysisHorizon === hour }" @click="analysisHorizon = hour">{{ hour }}h</button>
        </div>
        <div v-if="experimentSummary?.uncertainty.status === 'ready' && uncertaintyAt()" class="risk-summary">
          <div class="risk-radius"><span>90% 历史位置误差半径</span><strong>{{ uncertaintyAt()?.location_radius_90_km?.toFixed(0) ?? '--' }} km</strong></div>
          <div class="analysis-metric-list">
            <div><span>测试窗口覆盖率</span><strong>{{ formatPercent(uncertaintyAt()?.location_coverage_90) }}</strong></div>
            <div><span>整场台风覆盖率</span><strong>{{ formatPercent(uncertaintyAt()?.location_storm_coverage_90) }}</strong></div>
            <div><span>测试误差中位数</span><strong>{{ uncertaintyAt()?.median_error_km?.toFixed(1) ?? '--' }} km</strong></div>
            <div><span>校准目标</span><strong>{{ formatPercent(experimentSummary?.uncertainty.target_coverage) }}</strong></div>
          </div>
        </div>
        <div v-else class="message">{{ analysisError || '当前没有可用的历史校准范围。' }}</div>
        <div v-if="uncertaintyAt()" class="coverage-bar"><span :style="{ width: `${Math.min(100, (uncertaintyAt()?.location_coverage_90 ?? 0) * 100)}%` }"></span></div>
        <div class="risk-boundary">
          <strong>风险区数据状态</strong>
          <span>没有实时灾害风险概率、风暴潮或受灾影响范围数据，红色风险区不展示。</span>
        </div>
        <small class="panel-note">橙色范围是当前在线轨迹 CNN 精确 checkpoint 的历史 90% 位置校准半径，不是实时预报保证，也不是灾害影响半径；预测接口仍不返回 p05/p95 经纬度区间。</small>
      </template>
    </section>

    <footer v-if="selectedTyphoon" class="legacy-timeline">
      <div class="legacy-timeline-control">
        <button
          class="legacy-play-button"
          type="button"
          :disabled="!selectedTyphoon.points.length"
          :title="playing ? '暂停历史回顾' : '播放历史回顾'"
          @click="toggleReplay"
        >
          {{ playing ? 'Ⅱ' : '▶' }}
        </button>
        <div>
          <strong>历史回顾</strong>
          <small>{{ currentIndex + 1 }} / {{ selectedTyphoon.points.length }}</small>
        </div>
      </div>
      <div class="legacy-timeline-track">
        <input
          v-model.number="currentIndex"
          type="range"
          min="0"
          :max="Math.max(selectedTyphoon.points.length - 1, 0)"
          :style="{ '--progress': `${replayProgress}%` }"
          :disabled="!selectedTyphoon.points.length"
          aria-label="历史回顾进度"
        />
        <div class="legacy-timeline-dates">
          <span>{{ selectedTyphoon.points[0]?.time?.replace('T', ' ') ?? '--' }}</span>
          <span>{{ selectedTyphoon.points[selectedTyphoon.points.length - 1]?.time?.replace('T', ' ') ?? '--' }}</span>
        </div>
      </div>
      <div class="legacy-replay-readout">
        <strong>{{ currentPoint?.time?.replace('T', ' ') ?? '--' }}</strong>
        <span>{{ currentPoint?.lat.toFixed(1) ?? '--' }}°N · {{ currentPoint?.lng.toFixed(1) ?? '--' }}°E</span>
      </div>
      <button class="legacy-reset-button" type="button" :disabled="!selectedTyphoon.points.length" @click="resetTimeline">重置</button>
    </footer>
  </main>
</template>

<style scoped>
.screen {
  position: relative;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  background: #030b14;
}

.dashboard {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
}

.screen-header {
  position: fixed;
  top: 16px;
  right: 18px;
  left: 18px;
  z-index: 40;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 22px;
  pointer-events: none;
}

.screen-brand {
  min-width: 250px;
  padding-left: 12px;
  border-left: 3px solid #67e8d0;
  color: #e4ffff;
  text-shadow: 0 2px 12px rgba(0, 0, 0, 0.72);
  pointer-events: auto;
}

.screen-brand-kicker {
  display: block;
  color: #67e8d0;
  font-size: 10px;
  letter-spacing: 1px;
}

.screen-brand h1 {
  margin: 4px 0 2px;
  color: #f2ffff;
  font-size: 25px;
  font-weight: 600;
  line-height: 1.2;
}

.screen-brand-subtitle {
  display: block;
  color: #9ac0c5;
  font-size: 11px;
}

.top-actions {
  position: static;
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  max-width: min(820px, 70vw);
  pointer-events: auto;
}

.top-actions button,
.top-actions a {
  min-height: 42px;
  border: 1px solid rgba(103, 232, 208, 0.62);
  border-radius: 3px;
  padding: 0 14px;
  color: #d7ffff;
  background: rgba(4, 36, 52, 0.92);
  box-shadow: 0 5px 16px rgba(0, 0, 0, 0.2);
  cursor: pointer;
  font: 600 13px/1.2 'Microsoft YaHei', sans-serif;
  text-decoration: none;
  white-space: nowrap;
}

.top-actions button span {
  margin-left: 3px;
}

.top-actions button:hover,
.top-actions button.active,
.top-actions a:hover,
.close-button:hover {
  color: #06212a;
  background: #67e8d0;
}

.forecast-panel {
  position: fixed;
  top: 68px;
  right: 18px;
  bottom: 80px;
  z-index: 30;
  width: min(430px, calc(100vw - 36px));
  overflow: auto;
  padding: 16px;
  border: 1px solid rgba(92, 211, 210, 0.78);
  border-radius: 3px;
  color: #e4ffff;
  background: rgba(3, 24, 38, 0.96);
  box-shadow: 0 12px 35px #000a;
  font-family: 'Microsoft YaHei', sans-serif;
}

.panel-title {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.kicker {
  color: #67e8d0;
  font-size: 10px;
  letter-spacing: 1px;
}

.panel-title h2 {
  margin: 3px 0 0;
  font-size: 20px;
}

.panel-context {
  margin: 5px 0 0;
  color: #87a9ad;
  font-size: 11px;
}

.panel-mode-tabs {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 4px;
  margin: 0 0 13px;
  padding: 3px;
  border: 1px solid rgba(143, 207, 205, 0.2);
  background: rgba(8, 36, 48, 0.7);
}

.panel-mode-tabs button,
.metric-switch button {
  min-height: 28px;
  border: 0;
  border-radius: 2px;
  color: #91afb3;
  background: transparent;
  cursor: pointer;
  font-size: 11px;
}

.panel-mode-tabs button.active,
.metric-switch button.active {
  color: #06212a;
  background: #67e8d0;
}

.panel-mode-tabs button:hover,
.metric-switch button:hover {
  color: #e4ffff;
  background: rgba(103, 232, 208, 0.18);
}

.selection-block {
  margin-bottom: 14px;
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(143, 207, 205, 0.16);
}

.selection-block .field-label:first-child {
  margin-top: 0;
}

.history-overview,
.prediction-meta,
.source-meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  margin: 10px 0 14px;
  border: 1px solid rgba(143, 207, 205, 0.14);
  background: rgba(143, 207, 205, 0.12);
}

.history-overview div,
.prediction-meta div,
.source-meta div {
  display: grid;
  gap: 5px;
  min-height: 55px;
  padding: 9px;
  background: rgba(8, 29, 39, 0.92);
}

.history-overview span,
.prediction-meta span,
.source-meta span {
  color: #94b8be;
  font-size: 10px;
}

.history-overview strong,
.prediction-meta strong,
.source-meta strong {
  overflow-wrap: anywhere;
  color: #eaf6f5;
  font-size: 11px;
  font-weight: 600;
}

.map-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin-top: 13px;
  color: #b5ccd0;
  font-size: 10px;
  line-height: 1.4;
}

.legend-swatch {
  display: inline-block;
  width: 18px;
  height: 3px;
  margin-right: 4px;
  vertical-align: middle;
}

.legend-swatch.best-track {
  background: #b8c5cb;
}

.legend-swatch.replay-track,
.legend-swatch.forecast-track {
  background: #67e8d0;
}

.legend-swatch.forecast-track {
  background: repeating-linear-gradient(90deg, #67e8d0 0 5px, transparent 5px 8px);
}

.legend-swatch.error-range {
  height: 9px;
  border: 1px solid #f7c873;
  background: rgba(247, 200, 115, 0.2);
}

.section-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  margin: 18px 0 8px;
  color: #67e8d0;
  font-size: 12px;
  font-weight: 600;
}

.section-heading small {
  color: #749399;
  font-size: 10px;
  font-weight: 400;
}

.metric-switch {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px;
  margin-bottom: 4px;
  padding: 3px;
  border: 1px solid rgba(143, 207, 205, 0.18);
  background: rgba(14, 39, 51, 0.62);
}

.comparison-snapshot span {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.comparison-snapshot span small {
  color: #66838a;
  font-size: 9px;
  white-space: nowrap;
}

.audit-status,
.risk-boundary {
  display: grid;
  gap: 4px;
  margin-top: 13px;
  padding: 10px;
  border-left: 3px solid #67e8d0;
  color: #d8eeee;
  background: rgba(14, 54, 63, 0.65);
}

.audit-status.unavailable {
  border-left-color: #f7c873;
}

.audit-status span,
.risk-boundary strong {
  color: #f7c873;
  font-size: 11px;
}

.audit-status strong {
  color: #eaf6f5;
  font-size: 12px;
}

.audit-status small,
.risk-boundary span {
  color: #8daeb2;
  font-size: 10px;
  line-height: 1.5;
}

.close-button {
  width: 28px;
  height: 28px;
  border: 1px solid #377784;
  border-radius: 3px;
  color: #d7ffff;
  background: transparent;
  cursor: pointer;
  font-size: 18px;
}

.analysis-horizons button,
.horizon-group button {
  border: 1px solid rgba(143, 207, 205, 0.2);
  border-radius: 2px;
  color: #9dbabc;
  background: rgba(14, 39, 51, 0.62);
  cursor: pointer;
  font-size: 11px;
}

.analysis-horizons button.active,
.horizon-group button.active {
  color: #06212a;
  border-color: #67e8d0;
  background: #67e8d0;
}

.analysis-horizons button:hover,
.horizon-group button:hover {
  border-color: rgba(103, 232, 208, 0.72);
}

.model-line {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 8px 0 13px;
  color: #b5ccd0;
  font-size: 12px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #ff806f;
}

.status-dot.ready {
  background: #69e3b1;
  box-shadow: 0 0 9px rgba(105, 227, 177, 0.65);
}

.field-label {
  display: block;
  margin: 9px 0 4px;
  color: #94b8be;
  font-size: 11px;
}

.forecast-panel select {
  width: 100%;
  min-height: 33px;
  border: 1px solid #356a76;
  border-radius: 3px;
  padding: 6px 8px;
  color: #e4ffff;
  background: #0a2a38;
  font: 13px 'Microsoft YaHei', sans-serif;
}

.selection-info {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  margin: 12px 0;
  color: #70e2d3;
  font-size: 13px;
}

.selection-info span,
.prediction-readout span,
.environment-grid span,
.environment-grid small,
.comparison-snapshot span,
.analysis-metric-list span {
  color: #94b8be;
  font-size: 11px;
}

.horizon-group,
.analysis-horizons {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 4px;
  margin: 10px 0;
}

.horizon-group button,
.analysis-horizons button {
  min-height: 28px;
  padding: 5px 1px;
}

.prediction-readout {
  display: grid;
  gap: 8px;
  margin: 10px 0;
  padding: 10px;
  border-left: 3px solid #ff806f;
  background: #0b3441;
}

.prediction-readout div,
.environment-grid div,
.comparison-snapshot div,
.analysis-metric-list div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.prediction-readout strong,
.environment-grid strong,
.comparison-snapshot strong,
.analysis-metric-list strong {
  color: #fff;
  font-size: 12px;
  text-align: right;
}

.message {
  margin: 10px 0;
  color: #ffb1a2;
  font-size: 12px;
  line-height: 1.5;
}

.run-button {
  width: 100%;
  margin-top: 8px;
  border: 0;
  border-radius: 3px;
  padding: 10px;
  color: #06212a;
  background: #67e8d0;
  cursor: pointer;
  font-weight: 700;
}

.run-button:disabled {
  color: #789096;
  background: #2b4249;
  cursor: not-allowed;
}

.panel-note {
  display: block;
  margin-top: 12px;
  color: #749399;
  font-size: 10px;
  line-height: 1.5;
}

.environment-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  color: #d3eeee;
  font-size: 12px;
}

.environment-toolbar select {
  width: 132px;
}

.environment-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  margin-top: 12px;
  border: 1px solid rgba(143, 207, 205, 0.16);
  background: rgba(143, 207, 205, 0.13);
}

.environment-level-grid {
  margin-top: 10px;
}

.environment-grid div {
  display: grid;
  justify-content: stretch;
  min-height: 82px;
  padding: 11px;
  background: rgba(8, 29, 39, 0.92);
}

.environment-grid strong {
  font-size: 20px;
  color: #67e8d0;
}

.wind-vector-readout {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  padding: 12px;
  border-left: 3px solid #f7c873;
  background: rgba(14, 54, 63, 0.65);
}

.wind-vector-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-top: 10px;
}

.wind-vector-list .wind-vector-readout {
  min-width: 0;
  margin-top: 0;
  padding: 9px;
}

.wind-vector-readout small {
  display: block;
  margin-top: 4px;
  color: #87a9ad;
  font-size: 10px;
}

.wind-arrow {
  display: inline-block;
  color: #f7c873;
  font-size: 32px;
  line-height: 1;
}

.risk-summary {
  display: grid;
  gap: 12px;
}

.risk-radius {
  display: grid;
  gap: 5px;
  padding: 14px;
  border-left: 3px solid #f7c873;
  background: rgba(60, 48, 27, 0.55);
}

.risk-radius span {
  color: #c7b081;
  font-size: 11px;
}

.risk-radius strong {
  color: #f7c873;
  font-size: 27px;
  font-weight: 600;
}

.analysis-headline {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(143, 207, 205, 0.16);
  color: #67e8d0;
  font-size: 11px;
}

.analysis-headline strong {
  display: block;
  margin-top: 5px;
  color: #eaf6f5;
  font-size: 13px;
  font-weight: 600;
}

.comparison-snapshot,
.analysis-metric-list {
  display: grid;
  gap: 0;
  border-top: 1px solid rgba(143, 207, 205, 0.16);
  border-bottom: 1px solid rgba(143, 207, 205, 0.16);
}

.comparison-snapshot div,
.analysis-metric-list div {
  min-height: 34px;
  padding: 7px 0;
  border-bottom: 1px solid rgba(143, 207, 205, 0.1);
}

.comparison-snapshot div:last-child,
.analysis-metric-list div:last-child {
  border-bottom: 0;
}

.comparison-snapshot strong,
.analysis-metric-list strong {
  color: #f7c873;
}

.coverage-bar {
  height: 6px;
  margin-top: 14px;
  overflow: hidden;
  background: rgba(143, 207, 205, 0.14);
}

.coverage-bar span {
  display: block;
  height: 100%;
  background: #67e8d0;
}

.top-actions button:disabled {
  color: #789096;
  border-color: rgba(143, 207, 205, 0.24);
  background: rgba(4, 36, 52, 0.68);
  cursor: not-allowed;
}

.legacy-timeline {
  position: fixed;
  right: 0;
  bottom: 0;
  left: 0;
  z-index: 35;
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr) 250px 54px;
  gap: 14px;
  align-items: center;
  min-height: 72px;
  padding: 10px 20px;
  border-top: 1px solid rgba(103, 232, 208, 0.32);
  color: #e4ffff;
  background: rgba(3, 24, 38, 0.94);
  box-shadow: 0 -10px 28px rgba(0, 0, 0, 0.24);
  font-family: 'Microsoft YaHei', sans-serif;
}

.legacy-timeline-control,
.legacy-replay-readout,
.legacy-timeline-dates {
  display: flex;
  align-items: center;
}

.legacy-timeline-control {
  gap: 10px;
}

.legacy-timeline-control strong,
.legacy-replay-readout strong {
  display: block;
  color: #eaf6f5;
  font-size: 12px;
  font-weight: 600;
}

.legacy-timeline-control small,
.legacy-replay-readout span,
.legacy-timeline-dates {
  color: #7f9da7;
  font-size: 10px;
}

.legacy-timeline-control small {
  display: block;
  margin-top: 3px;
}

.legacy-play-button {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border: 1px solid rgba(103, 232, 208, 0.56);
  border-radius: 2px;
  color: #06212a;
  background: #67e8d0;
  cursor: pointer;
  font-size: 14px;
}

.legacy-play-button:disabled,
.legacy-reset-button:disabled {
  color: #789096;
  background: rgba(43, 66, 73, 0.8);
  cursor: not-allowed;
}

.legacy-timeline-track {
  min-width: 0;
}

.legacy-timeline-track input {
  display: block;
  width: 100%;
  height: 6px;
  margin: 4px 0 8px;
  appearance: none;
  border-radius: 3px;
  outline: none;
  background: linear-gradient(90deg, #67e8d0 0 var(--progress), rgba(143, 207, 205, 0.2) var(--progress) 100%);
  cursor: pointer;
}

.legacy-timeline-track input::-webkit-slider-thumb {
  width: 14px;
  height: 14px;
  appearance: none;
  border: 2px solid #06212a;
  border-radius: 50%;
  background: #f7c873;
  box-shadow: 0 0 0 2px #67e8d0;
}

.legacy-timeline-track input::-moz-range-thumb {
  width: 10px;
  height: 10px;
  border: 2px solid #06212a;
  border-radius: 50%;
  background: #f7c873;
  box-shadow: 0 0 0 2px #67e8d0;
}

.legacy-timeline-dates {
  justify-content: space-between;
  gap: 12px;
}

.legacy-replay-readout {
  display: grid;
  justify-content: end;
  gap: 3px;
  min-width: 0;
  text-align: right;
}

.legacy-replay-readout strong,
.legacy-replay-readout span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.legacy-reset-button {
  min-height: 30px;
  border: 1px solid rgba(143, 207, 205, 0.3);
  border-radius: 2px;
  color: #67e8d0;
  background: transparent;
  cursor: pointer;
  font-size: 11px;
}

.legacy-reset-button:hover {
  border-color: #67e8d0;
  background: rgba(103, 232, 208, 0.1);
}

@media (max-width: 760px) {
  .screen-header {
    top: 8px;
    right: 8px;
    left: 8px;
    flex-direction: column;
    gap: 8px;
  }

  .screen-brand {
    min-width: 0;
  }

  .screen-brand h1 {
    font-size: 20px;
  }

  .top-actions {
    width: 100%;
    max-width: none;
    justify-content: flex-start;
    gap: 5px;
  }

  .top-actions button,
  .top-actions a {
    min-height: 36px;
    padding: 0 9px;
    font-size: 11px;
  }

  .forecast-panel {
    top: 126px;
    right: 8px;
    bottom: 78px;
    width: calc(100vw - 16px);
  }

  .wind-vector-list {
    grid-template-columns: minmax(0, 1fr);
  }

  .history-overview,
  .prediction-meta,
  .source-meta {
    grid-template-columns: minmax(0, 1fr);
  }

  .legacy-timeline {
    grid-template-columns: 112px minmax(0, 1fr) 48px;
    gap: 8px;
    min-height: 66px;
    padding: 8px;
  }

  .legacy-replay-readout {
    display: none;
  }

  .legacy-timeline-dates {
    font-size: 8px;
  }
}
</style>
