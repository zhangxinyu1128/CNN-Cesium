<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import WorkspaceApp from './App.workspace.vue'
import ExperimentComparisonChart from '@/components/ExperimentComparisonChart.vue'
import TyphoonCharts from '@/components/TyphoonCharts.vue'
import {
  fetchExperimentSummary,
  fetchExportSettings,
  fetchHealth,
  fetchTyphoon,
  fetchTyphoonEra5,
  fetchTyphoonIndex,
  fetchYears,
  EXPORT_LEGEND_OPTIONS,
  EXPORT_INFO_OPTIONS,
  DEFAULT_EXPORT_INFO_IDS,
  DEFAULT_EXPORT_LEGEND_IDS,
  saveExportSettings,
  predictTyphoon
} from '@/services/typhoonApi'
import { buildPredictionWindow } from '@/services/predictionWindow'
import type {
  Era5Point,
  Era5Response,
  ExportInfoId,
  ExportLegendId,
  ExperimentModelSummary,
  ExperimentSummary,
  HealthResponse,
  PredictionResponse,
  TyphoonDetail,
  TyphoonIndexItem
} from '@/types/typhoon'

const showPanel = ref(true)
const showWorkspace = new URLSearchParams(window.location.search).get('workspace') === '1'
const panelMode = ref<'history' | 'forecast' | 'environment' | 'risk' | 'export'>('history')
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
const forecastSubpage = ref<'prediction' | 'comparison'>('prediction')
const comparisonMetric = ref<'mae' | 'rmse'>('mae')
const prediction = ref<PredictionResponse | null>(null)
const selectedLead = ref(24)
const loading = ref(false)
const loadingList = ref(false)
const message = ref('')
const iframeRef = ref<HTMLIFrameElement | null>(null)
const recording = ref(false)
const exportLegendIds = ref<ExportLegendId[]>([...DEFAULT_EXPORT_LEGEND_IDS])
const exportInfoIds = ref<ExportInfoId[]>([...DEFAULT_EXPORT_INFO_IDS])
const exportSettingsLoaded = ref(false)
let replayRecorder: MediaRecorder | null = null
let replayChunks: Blob[] = []
let recordingStopTimer: number | undefined
let recordingFrameId: number | undefined
let replayCaptureAttempt = 0
let replaySourceStream: MediaStream | null = null
let replayCompositeStream: MediaStream | null = null
let replaySourceVideo: HTMLVideoElement | null = null
let exportSettingsSaveTimer: number | undefined
let legacyListElement: HTMLElement | null = null

function applyLegacyListLayout(doc = iframeRef.value?.contentDocument) {
  if (!doc) return
  const style = doc.getElementById('main-screen-overrides') as HTMLStyleElement | null
  if (!style) return
  style.textContent = `
    .layout .layout-header { display: none !important; }
    html.main-screen-embedded .layout-main .list-panel .widget-panel {
      top: calc(10vh + 80px) !important;
      left: 4px !important;
      right: auto !important;
      bottom: auto !important;
      width: min(20vw, 420px) !important;
      height: min(720px, calc(100vh - 10vh - 172px)) !important;
      transform: none !important;
      box-sizing: border-box !important;
      resize: none !important;
    }
    html.main-screen-embedded .layout-main .list-panel .widget-panel .widget-panel-main {
      max-height: calc(100% - 35px) !important;
      overflow: hidden !important;
    }
    html.main-screen-embedded .layout-main .list-panel .el-table {
      height: min(54vh, 560px) !important;
    }
    html.main-screen-embedded .layout-main .list-panel .el-table__body-wrapper {
      max-height: none !important;
    }
  `
  const listElement = doc.querySelector<HTMLElement>('.layout-main .list-panel .widget-panel')
  if (listElement && listElement !== legacyListElement) {
    legacyListElement = listElement
  }
}

type LegacyEntity = Record<string, unknown>
type LegacyReplayPathEntity = LegacyEntity & {
  polyline?: {
    positions?: unknown
    show?: boolean
  }
}
type LegacyColor = {
  withAlpha: (alpha: number) => unknown
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
  scene?: { canvas?: HTMLCanvasElement; requestRender?: () => void }
}
type LegacyCesium = {
  Cartesian3: {
    fromDegrees: (lng: number, lat: number, height?: number) => unknown
    fromDegreesArray: (values: number[]) => unknown
  }
  Color: { fromCssColorString: (value: string) => LegacyColor }
  HeadingPitchRange: new (heading: number, pitch: number, range: number) => unknown
  Math: { toRadians: (degrees: number) => number }
  VerticalOrigin: { CENTER: number }
  LabelStyle: { FILL_AND_OUTLINE: number }
  PolylineGlowMaterialProperty?: new (options: Record<string, unknown>) => unknown
  PolylineDashMaterialProperty?: new (options: Record<string, unknown>) => unknown
  PolylineArrowMaterialProperty?: new (color: unknown) => unknown
}
type LegacyWindow = Window & { viewer?: LegacyViewer; Cesium?: LegacyCesium }
type LegacyHighlight = {
  entities: LegacyEntity[]
  marker: LegacyEntity
  typhoonIcon: LegacyEntity
  observationPoints: LegacyEntity[]
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
let legacyEra5Overlay: LegacyPredictionOverlay | null = null
const highlightColors = ['#ff1493', '#00fff0', '#ffe600', '#76ff03', '#ff6d00']
const typhoonIcon = `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(`
  <svg xmlns="http://www.w3.org/2000/svg" width="72" height="72" viewBox="0 0 72 72">
    <circle cx="36" cy="36" r="31" fill="#071d2b" fill-opacity=".9" stroke="#ffffff" stroke-width="3"/>
    <path d="M36 18c13 0 19 9 14 16-4 6-14 5-16-1-2-5 3-9 8-7 5 2 6 9 2 15-5 8-17 12-27 7" fill="none" stroke="#67e8d0" stroke-width="5" stroke-linecap="round"/>
    <circle cx="36" cy="36" r="4" fill="#ff806f" stroke="#ffffff" stroke-width="2"/>
  </svg>
`)}`
let legacyObserver: MutationObserver | null = null
let legacySyncTimer: number | undefined
let playTimer: number | undefined
const replayStepMs = 200
let legacyDblClickDocument: Document | null = null
let legacyDblClickWindow: Window | null = null
let legacyCheckboxDocument: Document | null = null
const legacyDblClickRows = new WeakSet<HTMLTableRowElement>()

const selectedPoint = computed(
  () => prediction.value?.predictions.find((point) => point.lead_hours === selectedLead.value) ?? null
)
const historicalWindRadius = computed(() => {
  const point = [...(selectedTyphoon.value?.points ?? [])].reverse().find(
    (item) => (item.radius7 ?? 0) > 0 || (item.radius10 ?? 0) > 0
  )
  return point?.radius7 && point.radius7 > 0 ? point.radius7 : point?.radius10 ?? null
})
const selectedImpactRadiusKm = computed(() => {
  if (!selectedPoint.value) return null
  const errorRadius = selectedPoint.value.location_radius_90_km ?? 0
  const windBasedRadius = Math.max(35, (selectedPoint.value.speed_ms ?? 0) * 2.5)
  return errorRadius + Math.max(windBasedRadius, historicalWindRadius.value ?? 0)
})
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
const forecastEra5Point = computed<Era5Point | null>(() => {
  const history = windowResult.value?.ok ? windowResult.value.history : []
  const target = canonicalTime(history[history.length - 1]?.time)
  return target
    ? era5.value?.points.find((item) => canonicalTime(item.time) === target) ?? null
    : null
})
const impactBearingDegrees = computed<number | null>(() => {
  const wind = forecastEra5Point.value?.levels['850']
  if (!wind) return null
  return (Math.atan2(wind.v, wind.u) * 180 / Math.PI + 360) % 360
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
  return model.by_horizon.find((item) => item.lead_hours === lead)
}

function formatPercent(value?: number | null) {
  return typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '--'
}

async function loadExportSettings() {
  try {
    const settings = await fetchExportSettings()
    exportLegendIds.value = DEFAULT_EXPORT_LEGEND_IDS.filter(
      (id) => settings.visible_legend_ids.includes(id)
    )
    exportInfoIds.value = DEFAULT_EXPORT_INFO_IDS.filter(
      (id) => settings.visible_info_ids?.includes(id) ?? true
    )
  } catch {
    message.value = '无法读取服务端的视频图例设置，当前使用默认选择'
  } finally {
    exportSettingsLoaded.value = true
  }
}

function setExportLegendEnabled(id: ExportLegendId, enabled: boolean) {
  const selected = new Set(exportLegendIds.value)
  if (enabled) selected.add(id)
  else selected.delete(id)
  exportLegendIds.value = DEFAULT_EXPORT_LEGEND_IDS.filter((item) => selected.has(item))
}

function setExportInfoEnabled(id: ExportInfoId, enabled: boolean) {
  const selected = new Set(exportInfoIds.value)
  if (enabled) selected.add(id)
  else selected.delete(id)
  exportInfoIds.value = DEFAULT_EXPORT_INFO_IDS.filter((item) => selected.has(item))
}

function setAllExportContent(enabled: boolean) {
  exportLegendIds.value = enabled ? [...DEFAULT_EXPORT_LEGEND_IDS] : []
  exportInfoIds.value = enabled ? [...DEFAULT_EXPORT_INFO_IDS] : []
}

function exportInfoEnabled(id: ExportInfoId) {
  return exportInfoIds.value.includes(id)
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
    ? '#4da3ff'
    : highlightColors[legacyHighlights.size % highlightColors.length]
  const lineColor = Cesium.Color.fromCssColorString(color)
  const replayColor = Cesium.Color.fromCssColorString('#4da3ff')
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
  const observationPoints = points.map((point, index) => viewer.entities.add({
    id: `MainObservation-${detail.tfbh}-${index}`,
    name: `${detail.name || detail.ename || detail.tfbh} 观测点 ${index + 1} · ${point.time}`,
    position: Cesium.Cartesian3.fromDegrees(point.lng, point.lat, 18000),
    point: {
      pixelSize: 9,
      color: Cesium.Color.fromCssColorString('#b8c0c5'),
      outlineColor: Cesium.Color.fromCssColorString('#102431'),
      outlineWidth: 2,
      disableDepthTestDistance: Number.POSITIVE_INFINITY
    }
  }))
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
  const icon = viewer.entities.add({
    id: `MainTyphoonIcon-${detail.tfbh}`,
    name: `${detail.name || detail.ename || detail.tfbh} 台风标识`,
    position: Cesium.Cartesian3.fromDegrees(replayPoint.lng, replayPoint.lat, 22000),
    billboard: {
      image: typhoonIcon,
      width: 42,
      height: 42,
      verticalOrigin: Cesium.VerticalOrigin.CENTER,
      disableDepthTestDistance: Number.POSITIVE_INFINITY
    },
    label: {
      text: detail.name || detail.ename || detail.tfbh,
      font: 'bold 13px Microsoft YaHei',
      fillColor: white,
      outlineColor: Cesium.Color.fromCssColorString('#06121c'),
      outlineWidth: 3,
      style: Cesium.LabelStyle.FILL_AND_OUTLINE,
      pixelOffset: { x: 0, y: -30 },
      showBackground: true,
      backgroundColor: Cesium.Color.fromCssColorString('#06121c').withAlpha(0.72)
    }
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
    entities: [line, replayTrail, marker, icon, ...observationPoints],
    marker,
    typhoonIcon: icon,
    observationPoints,
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
  highlight.typhoonIcon.position = Cesium.Cartesian3.fromDegrees(point.lng, point.lat, 22000)
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

function clearLegacyEra5() {
  const viewer = legacyFrame()?.viewer
  if (viewer && legacyEra5Overlay) {
    legacyEra5Overlay.entities.forEach((entity) => viewer.entities.remove(entity))
  }
  legacyEra5Overlay = null
}

function renderLegacyEra5() {
  clearLegacyEra5()
  const frame = legacyFrame()
  const Cesium = frame?.Cesium
  const viewer = frame?.viewer
  if (!Cesium || !viewer || !era5.value?.points.length) return

  const entities: LegacyEntity[] = []
  era5.value.points.forEach((sample) => {
    const level = sample.levels['850']
    if (!level || !Number.isFinite(level.speed_ms) || level.speed_ms <= 0) return
    const arrowLengthDegrees = Math.max(0.35, Math.min(1.6, level.speed_ms * 0.04))
    const latitudeScale = Math.max(Math.cos(Cesium.Math.toRadians(sample.lat)), 0.25)
    const endLng = sample.lng + (arrowLengthDegrees * level.u / level.speed_ms) / latitudeScale
    const endLat = sample.lat + arrowLengthDegrees * level.v / level.speed_ms
    entities.push(viewer.entities.add({
      id: `MainEra5Wind850-${selectedId.value}-${sample.time}`,
      name: `ERA5 850 hPa 沿轨风矢量 · ${sample.time} · ${level.speed_ms.toFixed(1)} m/s`,
      polyline: {
        positions: Cesium.Cartesian3.fromDegreesArray([sample.lng, sample.lat, endLng, endLat]),
        width: 3,
        material: Cesium.PolylineArrowMaterialProperty
          ? new Cesium.PolylineArrowMaterialProperty(Cesium.Color.fromCssColorString('#b8c0c5'))
          : Cesium.Color.fromCssColorString('#b8c0c5')
      }
    }))
  })
  legacyEra5Overlay = { entities }
  viewer.scene?.requestRender?.()
}

function renderLegacyPrediction() {
  clearLegacyPrediction()
  const frame = legacyFrame()
  const Cesium = frame?.Cesium
  const viewer = frame?.viewer
  const history = windowResult.value?.ok ? windowResult.value.history : []
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

  entities.push(viewer.entities.add({
    id: `MainPredictionOrigin-${selectedId.value}`,
    name: `CNN 预测起点 ${start.time}`,
    position: Cesium.Cartesian3.fromDegrees(start.lng, start.lat, 18000),
    point: { pixelSize: 10, color: originColor, outlineColor: lineColor, outlineWidth: 2 }
  }))
  forecastPoints.forEach((point) => {
    const region = point.uncertainty_region
    if (point.lead_hours === selectedLead.value && region && region.semi_major_axis_km > 0 && region.semi_minor_axis_km > 0) {
      entities.push(viewer.entities.add({
        id: `MainPredictionUncertainty-${selectedId.value}-${point.lead_hours}`,
        name: `+${point.lead_hours} 小时 · ${(region.coverage * 100).toFixed(0)}% 历史校准二维位置区域`,
        position: Cesium.Cartesian3.fromDegrees(point.lng, point.lat, 18000),
        ellipse: {
          semiMajorAxis: region.semi_major_axis_km * 1000,
          semiMinorAxis: region.semi_minor_axis_km * 1000,
          rotation: Cesium.Math.toRadians(region.bearing_deg ?? 0),
          material: errorColor.withAlpha(0.10),
          outline: true,
          outlineColor: errorColor.withAlpha(0.72),
          outlineWidth: 1,
          height: 18000
        }
      }))
    }
    if (point.lead_hours === selectedLead.value) {
      const errorRadius = point.location_radius_90_km ?? 0
      const windBasedRadius = Math.max(35, (point.speed_ms ?? 0) * 2.5)
      const impactRadius = errorRadius + Math.max(windBasedRadius, historicalWindRadius.value ?? 0)
      entities.push(viewer.entities.add({
        id: `MainPredictionImpact-${selectedId.value}-${point.lead_hours}`,
        name: `+${point.lead_hours} 小时 · 模型估计影响范围 · 半径 ${Math.round(impactRadius)} km`,
        position: Cesium.Cartesian3.fromDegrees(point.lng, point.lat, 17500),
        ellipse: {
          semiMajorAxis: impactRadius * 1000,
          semiMinorAxis: impactRadius * 1000 * 0.72,
          rotation: Cesium.Math.toRadians(impactBearingDegrees.value ?? region?.bearing_deg ?? 0),
          material: Cesium.Color.fromCssColorString('#ef4444').withAlpha(0.08),
          outline: true,
          outlineColor: Cesium.Color.fromCssColorString('#ef4444').withAlpha(0.74),
          outlineWidth: 2,
          height: 17500
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

function legacyRowId(row: HTMLTableRowElement) {
  const candidates = row.textContent?.match(/\b\d{4,8}\b/g) ?? []
  return candidates.find((candidate) => typhoons.value.some((item) => item.tfbh === candidate))
    ?? candidates[0]
}

function handleLegacyCheckboxChange(event: Event) {
  const checkbox = event.target as HTMLInputElement | null
  const doc = legacyCheckboxDocument
  if (!checkbox?.matches('input[type="checkbox"]') || !doc) return
  const row = checkbox.closest('tr') as HTMLTableRowElement | null
  const id = row ? legacyRowId(row) : undefined
  if (!id) return

  if (checkbox.checked) {
    selectedId.value = id
    requestLegacyHighlightSync(id)
    void loadSelected(id)
    return
  }

  removeLegacyHighlight(id)
  if (id === selectedId.value) {
    stopReplay()
    prediction.value = null
    era5.value = null
    selectedTyphoon.value = null
    clearLegacyPrediction()
    clearLegacyEra5()
  }
  requestLegacyHighlightSync()
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
  const ids = new Set(checkedLegacyIds())
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
  applyLegacyListLayout(doc)
  window.setTimeout(renderLegacyEra5, 1200)
  doc.querySelectorAll('a[href*="workspace"]').forEach((link) => {
    ;(link as HTMLElement).style.display = 'none'
  })
  legacyDblClickDocument?.removeEventListener('dblclick', handleLegacyTyphoonDblClick, true)
  legacyDblClickWindow?.removeEventListener('dblclick', handleLegacyTyphoonDblClick, true)
  legacyCheckboxDocument?.removeEventListener('change', handleLegacyCheckboxChange, true)
  legacyDblClickDocument = doc
  legacyDblClickWindow = doc.defaultView
  legacyCheckboxDocument = doc
  doc.addEventListener('dblclick', handleLegacyTyphoonDblClick, true)
  doc.addEventListener('change', handleLegacyCheckboxChange, true)
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
      if (selectedTyphoon.value?.tfbh === id && checkedLegacyIds().includes(id)) {
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
  const window_ = windowResult.value
  if (!selectedTyphoon.value || !window_?.ok) {
    message.value = (window_ && !window_.ok ? window_.reason : null) || '当前台风没有足够的连续历史观测。'
    return
  }
  if (!modelReady.value) {
    message.value = health.value?.model.reason || '模型不可用，请先安装 PyTorch。'
    return
  }
  loading.value = true
  message.value = ''
  try {
    prediction.value = await predictTyphoon(selectedId.value, window_.history)
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

function downloadRiskGeoJson() {
  if (!selectedTyphoon.value || !prediction.value) {
    message.value = '请先选择台风并运行 CNN 预测'
    return
  }
  const ellipseCoordinates = (
    point: PredictionResponse['predictions'][number],
    semiMajorKm: number,
    semiMinorKm: number,
    bearingDegrees: number
  ) => {
    const bearing = bearingDegrees * Math.PI / 180
    return Array.from({ length: 73 }, (_, index) => {
      const theta = 2 * Math.PI * index / 72
      const east = semiMajorKm * Math.sin(theta) * Math.sin(bearing)
        + semiMinorKm * Math.cos(theta) * Math.cos(bearing)
      const north = semiMajorKm * Math.sin(theta) * Math.cos(bearing)
        - semiMinorKm * Math.cos(theta) * Math.sin(bearing)
      const lat = point.lat + north / 111.195
      const lng = point.lng + east / (111.195 * Math.max(Math.cos(lat * Math.PI / 180), 0.1))
      return [Number(lng.toFixed(6)), Number(lat.toFixed(6))]
    })
  }
  const features = prediction.value.predictions.flatMap((point) => {
    const region = point.uncertainty_region
    const regionFeature = region && region.semi_major_axis_km > 0 && region.semi_minor_axis_km > 0
      ? [{
          type: 'Feature',
          properties: {
            layer: 'historical_calibration_error',
            typhoon_id: selectedTyphoon.value?.tfbh,
            lead_hours: point.lead_hours,
            coverage: region.coverage,
            region_geometry: region.geometry,
            interpretation: region.interpretation ?? '历史校准二维位置区域，不是实时灾害概率区'
          },
          geometry: {
            type: 'Polygon',
            coordinates: [ellipseCoordinates(
              point,
              region.semi_major_axis_km,
              region.semi_minor_axis_km,
              region.bearing_deg ?? 0
            )]
          }
        }]
      : []
    const errorRadius = point.location_radius_90_km ?? 0
    const windBasedRadius = Math.max(35, (point.speed_ms ?? 0) * 2.5)
    const impactRadius = errorRadius + Math.max(windBasedRadius, historicalWindRadius.value ?? 0)
    const impactFeature = {
      type: 'Feature',
      properties: {
        layer: 'heuristic_estimated_impact',
        typhoon_id: selectedTyphoon.value?.tfbh,
        lead_hours: point.lead_hours,
        estimated_radius_km: Number(impactRadius.toFixed(1)),
        predicted_wind_speed_ms: point.speed_ms,
        historical_wind_radius_km: historicalWindRadius.value,
        location_calibration_radius_km: point.location_radius_90_km ?? null,
        era5_sample_time: forecastEra5Point.value?.time ?? null,
        era5_850_u_ms: forecastEra5Point.value?.levels['850']?.u ?? null,
        era5_850_v_ms: forecastEra5Point.value?.levels['850']?.v ?? null,
        era5_850_speed_ms: forecastEra5Point.value?.levels['850']?.speed_ms ?? null,
        era5_steering_bearing_deg: impactBearingDegrees.value,
        era5_context: 'ERA5 850 hPa track-aligned wind or nearest matching sample; environmental context, not CNN input',
        interpretation: 'Heuristic estimate; not a hazard probability, observed impact footprint, or official warning'
      },
        geometry: {
        type: 'Polygon',
        coordinates: [ellipseCoordinates(point, impactRadius, impactRadius * 0.72, impactBearingDegrees.value ?? region?.bearing_deg ?? 0)]
      }
    }
    return [...regionFeature, impactFeature]
  })
  const payload = {
    type: 'FeatureCollection',
    name: `${selectedTyphoon.value.tfbh}-prediction-and-impact-estimates`,
    properties: { exported_at: new Date().toISOString(), source: prediction.value.model_version },
    features
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/geo+json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${selectedTyphoon.value.tfbh}-prediction-impact-ranges.geojson`
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

function exportCanvasPng(kind: 'screenshot' | 'wind') {
  if (kind === 'wind' && !era5.value?.points.length) {
    message.value = '当前台风没有可导出的 ERA5 沿轨风矢量样本'
    return
  }
  const canvas = legacyFrame()?.viewer?.scene?.canvas
    ?? iframeRef.value?.contentDocument?.querySelector<HTMLCanvasElement>('.cesium-viewer canvas, canvas')
  if (!canvas) {
    message.value = '地图尚未准备好，暂时无法导出 PNG'
    return
  }
  try {
    legacyFrame()?.viewer?.scene?.requestRender?.()
    legacyFrame()?.viewer && typeof (legacyFrame()?.viewer as LegacyViewer & { render?: () => void }).render === 'function'
      && (legacyFrame()?.viewer as LegacyViewer & { render: () => void }).render()
    let exportCanvas = canvas
    if (kind === 'wind') {
      exportCanvas = document.createElement('canvas')
      exportCanvas.width = canvas.width
      exportCanvas.height = canvas.height
      const exportContext = exportCanvas.getContext('2d')
      if (!exportContext) throw new Error('ERA5 export canvas is unavailable')
      exportContext.drawImage(canvas, 0, 0, exportCanvas.width, exportCanvas.height)
      drawWindExportPanel(exportContext, exportCanvas.width, exportCanvas.height)
    } else {
      exportCanvas = document.createElement('canvas')
      exportCanvas.width = canvas.width
      exportCanvas.height = canvas.height
      const exportContext = exportCanvas.getContext('2d')
      if (!exportContext) throw new Error('Screenshot export canvas is unavailable')
      exportContext.drawImage(canvas, 0, 0, exportCanvas.width, exportCanvas.height)
      drawReplayLegend(exportContext, exportCanvas.width, exportCanvas.height)
    }
    const url = exportCanvas.toDataURL('image/png')
    const link = document.createElement('a')
    const id = selectedTyphoon.value?.tfbh ?? 'typhoon'
    link.href = url
    link.download = `${id}-${kind === 'wind' ? 'ERA5风场图' : '三维研判截图'}.png`
    link.click()
    message.value = kind === 'wind'
      ? '已导出 ERA5 沿轨风矢量 PNG（地图右侧附风向、风速和样本时刻）'
      : '已导出当前三维研判视图 PNG'
  } catch {
    message.value = '地图图层无法转为 PNG，请确认浏览器允许跨域地图瓦片导出'
  }
}

function drawWindExportPanel(
  context: CanvasRenderingContext2D,
  width: number,
  height: number
) {
  const scale = Math.max(0.72, Math.min(1, width / 1440, (height - 24) / 520))
  const padding = 18 * scale
  const panelWidth = Math.min(width - padding * 2, 420 * scale)
  const panelHeight = Math.min(height - padding * 2, 500 * scale)
  const panelX = width - panelWidth - padding
  const panelY = padding
  const left = panelX + 18 * scale
  const right = panelX + panelWidth - 18 * scale
  const lineHeight = 25 * scale
  const windPoints = (era5.value?.points ?? [])
    .filter((point) => point.levels['850'] && Number.isFinite(point.levels['850'].speed_ms))
    .slice(-8)
  const current = selectedEra5Point.value?.levels['850']

  context.fillStyle = 'rgba(3, 18, 29, 0.94)'
  context.fillRect(panelX, panelY, panelWidth, panelHeight)
  context.strokeStyle = '#f7c873'
  context.lineWidth = Math.max(1, 2 * scale)
  context.strokeRect(panelX, panelY, panelWidth, panelHeight)

  let y = panelY + padding + 16 * scale
  context.fillStyle = '#fff4cf'
  context.font = `700 ${18 * scale}px "Microsoft YaHei", Arial, sans-serif`
  context.fillText('ERA5 850 hPa 沿轨风矢量', left, y)
  y += lineHeight
  context.fillStyle = '#c7d8db'
  context.font = `500 ${13 * scale}px "Microsoft YaHei", Arial, sans-serif`
  context.fillText(`${selectedId.value} · ${selectedTyphoon.value?.name || selectedTyphoon.value?.ename || ''}`, left, y)
  y += lineHeight
  if (exportInfoEnabled('era5')) {
    context.fillStyle = '#ffffff'
    context.font = `600 ${14 * scale}px "Microsoft YaHei", Arial, sans-serif`
    context.fillText(`当前风速  ${current ? `${current.speed_ms.toFixed(1)} m/s` : '无匹配样本'}`, left, y)
    y += lineHeight
    context.fillText(`数据样本  ${windPoints.length} 个 · 来源 ERA5`, left, y)
    y += 10 * scale
  }

  context.strokeStyle = 'rgba(247, 200, 115, 0.48)'
  context.beginPath()
  context.moveTo(left, y)
  context.lineTo(right, y)
  context.stroke()
  y += 22 * scale
  if (exportInfoEnabled('era5')) {
    context.fillStyle = '#f7c873'
    context.font = `700 ${14 * scale}px "Microsoft YaHei", Arial, sans-serif`
    context.fillText('匹配时刻与风向', left, y)
    y += 8 * scale
  }

  for (const point of exportInfoEnabled('era5') ? windPoints : []) {
    if (y > panelY + panelHeight - 48 * scale) break
    const wind = point.levels['850']
    if (!wind) continue
    y += lineHeight
    const arrowX = left + 12 * scale
    const arrowY = y - 5 * scale
    const angle = Math.atan2(wind.v, wind.u)
    const arrowLength = 84 * scale
    context.strokeStyle = '#b8c0c5'
    context.fillStyle = '#b8c0c5'
    context.lineWidth = Math.max(1, 2 * scale)
    context.beginPath()
    context.moveTo(arrowX, arrowY)
    context.lineTo(arrowX + Math.cos(angle) * arrowLength, arrowY - Math.sin(angle) * arrowLength)
    context.stroke()
    context.beginPath()
    context.moveTo(arrowX + Math.cos(angle) * arrowLength, arrowY - Math.sin(angle) * arrowLength)
    context.lineTo(arrowX + Math.cos(angle + 2.55) * 21 * scale, arrowY - Math.sin(angle + 2.55) * 21 * scale)
    context.lineTo(arrowX + Math.cos(angle - 2.55) * 21 * scale, arrowY - Math.sin(angle - 2.55) * 21 * scale)
    context.closePath()
    context.fill()
    context.fillStyle = '#e5f1f3'
    context.font = `500 ${12 * scale}px "Microsoft YaHei", Arial, sans-serif`
    const time = point.time.replace('T', ' ').slice(0, 16)
    context.fillText(`${time}   ${wind.speed_ms.toFixed(1)} m/s   ${wind.direction_deg.toFixed(0)}°`, left + 108 * scale, y)
  }

  if (exportInfoEnabled('era5')) {
    context.fillStyle = '#9fb8bd'
    context.font = `500 ${12 * scale}px "Microsoft YaHei", Arial, sans-serif`
    context.fillText('风矢量图为环境参考，不代表在线 CNN 输入。', left, panelY + panelHeight - 24 * scale)
  }
}

function drawReplayLegend(
  context: CanvasRenderingContext2D,
  width: number,
  height: number
) {
  const scale = Math.max(0.72, Math.min(1, width / 1440, (height - 24) / 450))
  const padding = 18 * scale
  const panelWidth = Math.min(width - padding * 2, 390 * scale)
  const panelX = width - panelWidth - padding
  const panelY = padding
  const panelHeight = 445 * scale
  const lineHeight = 24 * scale
  const left = panelX + 18 * scale
  const right = panelX + panelWidth - 18 * scale
  const forecast = selectedPoint.value
  const stormName = selectedTyphoon.value?.name || selectedTyphoon.value?.ename || selectedId.value

  context.fillStyle = 'rgba(3, 18, 29, 0.9)'
  context.fillRect(panelX, panelY, panelWidth, panelHeight)
  context.strokeStyle = 'rgba(132, 205, 210, 0.85)'
  context.lineWidth = Math.max(1, scale)
  context.strokeRect(panelX, panelY, panelWidth, panelHeight)

  let y = panelY + padding + 14 * scale
  context.textAlign = 'left'
  context.textBaseline = 'alphabetic'
  context.fillStyle = '#f5fbff'
  context.font = `700 ${18 * scale}px "Microsoft YaHei", Arial, sans-serif`
  context.fillText('台风回放研判', left, y)
  y += lineHeight

  context.fillStyle = '#a9c6ce'
  context.font = `500 ${14 * scale}px "Microsoft YaHei", Arial, sans-serif`
  context.fillText(`${selectedId.value} · ${stormName}`, left, y)
  y += lineHeight

  const current = currentPoint.value
  context.fillStyle = '#ffffff'
  context.font = `600 ${14 * scale}px "Microsoft YaHei", Arial, sans-serif`
  if (exportInfoEnabled('time')) {
    context.fillText(`回放时间  ${current?.time?.replace('T', ' ') ?? '--'}`, left, y)
    y += lineHeight
  }
  if (exportInfoEnabled('position')) {
    context.fillText(
      `位置  ${current ? `${current.lat.toFixed(1)}°N · ${current.lng.toFixed(1)}°E` : '--'}`,
      left,
      y
    )
    y += lineHeight
  }
  if (exportInfoEnabled('intensity')) {
    context.fillText(
      `历史强度  ${current?.strong || '--'} · ${typeof current?.speed === 'number' ? `${current.speed.toFixed(1)} m/s` : '--'}`,
      left,
      y
    )
    y += lineHeight
  }
  if (exportInfoEnabled('era5')) {
    context.fillText(
      `ERA5 850 hPa  ${currentWind850.value ? `${currentWind850.value.speed_ms.toFixed(1)} m/s` : '无匹配样本'}`,
      left,
      y
    )
    y += lineHeight
  }
  y += 12 * scale

  context.strokeStyle = 'rgba(132, 205, 210, 0.42)'
  context.beginPath()
  context.moveTo(left, y)
  context.lineTo(right, y)
  context.stroke()
  y += 20 * scale

  const legendRows = EXPORT_LEGEND_OPTIONS
    .filter((item) => exportLegendIds.value.includes(item.id))
    .map((item) => ({
      ...item,
      available: item.id === 'history'
        || (item.id === 'error' ? Boolean(forecast?.uncertainty_region) : Boolean(forecast))
    }))

  if (legendRows.length) {
    context.fillStyle = '#d5e9ee'
    context.font = `700 ${14 * scale}px "Microsoft YaHei", Arial, sans-serif`
    context.fillText('图例', left, y)
    y += 8 * scale

    for (const item of legendRows) {
      y += lineHeight
      context.globalAlpha = item.available ? 1 : 0.52
      context.strokeStyle = item.color
      context.fillStyle = item.color
      if (item.kind === 'line' || item.kind === 'dashed') {
        context.lineWidth = 3 * scale
        context.setLineDash(item.kind === 'dashed' ? [7 * scale, 5 * scale] : [])
        context.beginPath()
        context.moveTo(left, y - 4 * scale)
        context.lineTo(left + 26 * scale, y - 4 * scale)
        context.stroke()
        context.setLineDash([])
      } else {
        context.globalAlpha = item.available ? 0.9 : 0.42
        context.fillRect(left, y - 13 * scale, 24 * scale, 13 * scale)
        context.globalAlpha = item.available ? 1 : 0.52
        context.lineWidth = scale
        context.strokeRect(left, y - 13 * scale, 24 * scale, 13 * scale)
      }
      context.globalAlpha = 1
      context.fillStyle = item.available ? '#e5f1f3' : '#91a6ac'
      context.font = `500 ${14 * scale}px "Microsoft YaHei", Arial, sans-serif`
      context.fillText(`${item.label}${item.available ? '' : '（未生成）'}`, left + 36 * scale, y)
    }

    y += 12 * scale
    context.strokeStyle = 'rgba(132, 205, 210, 0.42)'
    context.beginPath()
    context.moveTo(left, y)
    context.lineTo(right, y)
    context.stroke()
    y += 20 * scale
  }
  context.fillStyle = '#ffffff'
  context.font = `600 ${13 * scale}px "Microsoft YaHei", Arial, sans-serif`
  if (forecast) {
    context.fillText(
      `预测 +${forecast.lead_hours} h · ${forecast.speed_ms?.toFixed(1) ?? '--'} m/s`,
      left,
      y
    )
    y += lineHeight
    const region = forecast.uncertainty_region
    context.fillText(
      `误差椭圆  ${region ? `${region.semi_major_axis_km.toFixed(0)} × ${region.semi_minor_axis_km.toFixed(0)} km` : '无校准结果'}`,
      left,
      y
    )
    y += lineHeight
    context.fillText(`估计影响半径  ${selectedImpactRadiusKm.value?.toFixed(0) ?? '--'} km`, left, y)
    y += lineHeight
  } else {
    context.fillText('尚未运行 CNN 预测', left, y)
    y += lineHeight * 2
  }

  context.fillStyle = '#a9c0c6'
  context.font = `500 ${12 * scale}px "Microsoft YaHei", Arial, sans-serif`
  context.fillText('ERA5 仅作环境参考；在线模型为轨迹 CNN。', left, y)
  y += 16 * scale
  context.fillText('误差为历史校准范围，红色区域不是预警或灾害概率。', left, y)
}

function releaseReplayCapture(
  sourceVideo: HTMLVideoElement | null = replaySourceVideo,
  sourceStream: MediaStream | null = replaySourceStream,
  compositeStream: MediaStream | null = replayCompositeStream
) {
  sourceVideo?.pause()
  if (sourceVideo) {
    sourceVideo.srcObject = null
    sourceVideo.remove()
  }
  sourceStream?.getTracks().forEach((track) => track.stop())
  compositeStream?.getTracks().forEach((track) => track.stop())
  if (replaySourceVideo === sourceVideo) replaySourceVideo = null
  if (replaySourceStream === sourceStream) replaySourceStream = null
  if (replayCompositeStream === compositeStream) replayCompositeStream = null
}

function finishReplayRecording() {
  if (!recording.value && !replayRecorder && !replaySourceVideo) return
  replayCaptureAttempt += 1
  stopReplay()
  recording.value = false
  if (recordingFrameId !== undefined) {
    window.cancelAnimationFrame(recordingFrameId)
    recordingFrameId = undefined
  }
  if (recordingStopTimer) window.clearTimeout(recordingStopTimer)
  recordingStopTimer = undefined
  if (replayRecorder && replayRecorder.state !== 'inactive') replayRecorder.stop()
  else releaseReplayCapture()
}

async function recordReplayVideo() {
  if (recording.value) {
    finishReplayRecording()
    return
  }
  const canvas = legacyFrame()?.viewer?.scene?.canvas
    ?? iframeRef.value?.contentDocument?.querySelector<HTMLCanvasElement>('.cesium-viewer canvas, canvas')
  if (!canvas || typeof canvas.captureStream !== 'function' || typeof MediaRecorder === 'undefined') {
    message.value = '当前浏览器不支持地图 MP4 回放录制'
    return
  }
  const attempt = ++replayCaptureAttempt
  recording.value = true
  message.value = '正在准备地图画面…'
  let mediaRecorder: MediaRecorder | null = null
  let sourceStream: MediaStream | null = null
  let compositeStream: MediaStream | null = null
  let sourceVideo: HTMLVideoElement | null = null
  try {
    replayChunks = []
    sourceStream = canvas.captureStream(12)
    replaySourceStream = sourceStream
    sourceVideo = document.createElement('video')
    sourceVideo.muted = true
    sourceVideo.playsInline = true
    sourceVideo.autoplay = true
    sourceVideo.style.position = 'fixed'
    sourceVideo.style.left = '-10000px'
    sourceVideo.style.top = '0'
    replaySourceVideo = sourceVideo
    sourceVideo.srcObject = sourceStream
    document.body.append(sourceVideo)
    await sourceVideo.play()
    if (sourceVideo.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
      await new Promise<void>((resolve, reject) => {
        const timeout = window.setTimeout(() => reject(new Error('Map video frame timed out')), 5000)
        sourceVideo?.addEventListener('loadeddata', () => {
          window.clearTimeout(timeout)
          resolve()
        }, { once: true })
        sourceVideo?.addEventListener('error', () => {
          window.clearTimeout(timeout)
          reject(new Error('Map video frame could not be decoded'))
        }, { once: true })
      })
    }
    if (attempt !== replayCaptureAttempt || !recording.value) {
      releaseReplayCapture(sourceVideo, sourceStream, compositeStream)
      return
    }
    if (!sourceVideo.videoWidth || !sourceVideo.videoHeight) {
      throw new Error('Map video stream has no decoded frame')
    }
    await new Promise<void>((resolve) => {
      window.requestAnimationFrame(() => window.requestAnimationFrame(() => resolve()))
    })
    if (attempt !== replayCaptureAttempt || !recording.value) {
      releaseReplayCapture(sourceVideo, sourceStream, compositeStream)
      return
    }

    const recordingCanvas = document.createElement('canvas')
    recordingCanvas.width = canvas.width
    recordingCanvas.height = canvas.height
    const context = recordingCanvas.getContext('2d')
    if (!context) throw new Error('2D video overlay canvas is unavailable')
    const drawFrame = () => {
      if (!sourceVideo || !recording.value || attempt !== replayCaptureAttempt) return
      context.drawImage(sourceVideo, 0, 0, recordingCanvas.width, recordingCanvas.height)
      drawReplayLegend(context, recordingCanvas.width, recordingCanvas.height)
      recordingFrameId = window.requestAnimationFrame(drawFrame)
    }
    drawFrame()
    compositeStream = recordingCanvas.captureStream(8)
    replayCompositeStream = compositeStream
    const mimeType = MediaRecorder.isTypeSupported('video/mp4;codecs=h264')
      ? 'video/mp4;codecs=h264'
      : MediaRecorder.isTypeSupported('video/webm;codecs=vp9')
        ? 'video/webm;codecs=vp9'
        : 'video/webm'
    mediaRecorder = new MediaRecorder(compositeStream, { mimeType })
    replayRecorder = mediaRecorder
    mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) replayChunks.push(event.data)
    }
    mediaRecorder.onstop = () => {
      const extension = mimeType.startsWith('video/mp4') ? 'mp4' : 'webm'
      const blob = new Blob(replayChunks, { type: mimeType })
      releaseReplayCapture(sourceVideo, sourceStream, compositeStream)
      recording.value = false
      replayRecorder = null
      replayChunks = []
      if (blob.size === 0) {
        message.value = '视频没有录到有效画面，请重试'
        return
      }
      const link = document.createElement('a')
      const videoUrl = URL.createObjectURL(blob)
      link.href = videoUrl
      link.download = `${selectedTyphoon.value?.tfbh ?? 'typhoon'}-历史回放.${extension}`
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(videoUrl), 1000)
      message.value = extension === 'mp4'
        ? 'MP4 历史回放已导出'
        : '浏览器仅支持 WebM，已导出 WebM 历史回放'
    }
    mediaRecorder.start(250)
    currentIndex.value = 0
    playing.value = false
    toggleReplay()
    const frameCount = selectedTyphoon.value?.points.length ?? 0
    recordingStopTimer = window.setTimeout(
      () => finishReplayRecording(),
      Math.max(500, (frameCount - 1) * replayStepMs + 350)
    )
    message.value = '正在录制历史回放，结束后自动下载视频'
  } catch {
    releaseReplayCapture(sourceVideo, sourceStream, compositeStream)
    if (attempt !== replayCaptureAttempt) return
    if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop()
    recording.value = false
    if (!mediaRecorder || mediaRecorder.state === 'inactive') replayRecorder = null
    message.value = '无法读取三维地图画面或启动视频录制，请重试'
  }
}

function openPanel(mode: 'history' | 'forecast' | 'environment' | 'risk' | 'export') {
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

watch(selectedLead, () => requestLegacyPredictionRender())

watch(prediction, () => requestLegacyPredictionRender(), { deep: true })
watch(era5, () => renderLegacyEra5(), { deep: true })
watch(exportLegendIds, (ids) => {
  if (!exportSettingsLoaded.value) return
  if (exportSettingsSaveTimer) window.clearTimeout(exportSettingsSaveTimer)
  const visible_legend_ids = [...ids]
  exportSettingsSaveTimer = window.setTimeout(async () => {
    exportSettingsSaveTimer = undefined
    try {
      await saveExportSettings({ visible_legend_ids, visible_info_ids: [...exportInfoIds.value] })
    } catch {
      message.value = '导出显示选项未能保存到服务端'
    }
  }, 250)
}, { deep: true })
watch(exportInfoIds, () => {
  if (!exportSettingsLoaded.value) return
  if (exportSettingsSaveTimer) window.clearTimeout(exportSettingsSaveTimer)
  exportSettingsSaveTimer = window.setTimeout(async () => {
    exportSettingsSaveTimer = undefined
    try {
      await saveExportSettings({
        visible_legend_ids: [...exportLegendIds.value],
        visible_info_ids: [...exportInfoIds.value]
      })
    } catch {
      message.value = '导出显示选项未能保存到服务端'
    }
  }, 250)
}, { deep: true })

onUnmounted(() => {
  stopReplay()
  finishReplayRecording()
  clearLegacyPrediction()
  clearLegacyEra5()
  legacyDblClickDocument?.removeEventListener('dblclick', handleLegacyTyphoonDblClick, true)
  legacyDblClickWindow?.removeEventListener('dblclick', handleLegacyTyphoonDblClick, true)
  legacyCheckboxDocument?.removeEventListener('change', handleLegacyCheckboxChange, true)
  legacyObserver?.disconnect()
  if (legacySyncTimer) window.clearTimeout(legacySyncTimer)
  if (exportSettingsSaveTimer) window.clearTimeout(exportSettingsSaveTimer)
})

onMounted(async () => {
  health.value = await fetchHealth().catch(() => null)
  years.value = await fetchYears().catch(() => [])
  if (!years.value.includes(selectedYear.value)) selectedYear.value = years.value[0] ?? 2024
  await Promise.all([loadList(), loadAnalysisSummary(), loadExportSettings()])
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
        <div class="screen-brand-title-row">
          <h1>历年台风展示监控平台</h1>
          <span class="screen-brand-subtitle">轨迹回放 · CNN 预测 · ERA5 风场</span>
        </div>
      </div>

      <nav class="top-actions" aria-label="功能模块">
        <button type="button" title="回到大屏全局视角" @click="resetGlobal">⌂ <span>全局视角</span></button>
        <button type="button" :class="{ active: showPanel && panelMode === 'history' }" title="打开历史路径面板" @click="openPanel('history')">⌁ <span>历史路径</span></button>
        <button type="button" :class="{ active: showPanel && panelMode === 'forecast' }" title="打开模型预测面板" @click="openPanel('forecast')">◈ <span>模型预测</span></button>
        <button type="button" :class="{ active: showPanel && panelMode === 'environment' }" title="打开 ERA5 环境场面板" @click="openPanel('environment')">↗ <span>环境风场</span></button>
        <button type="button" :class="{ active: showPanel && panelMode === 'risk' }" title="打开风险研判面板" @click="openPanel('risk')">△ <span>风险研判</span></button>
        <button type="button" :class="{ active: showPanel && panelMode === 'export' }" title="打开导出面板" @click="openPanel('export')">⇩ <span>导出</span></button>
      </nav>
    </header>

    <section
      v-if="showPanel"
      ref="panelElement"
      class="forecast-panel analysis-panel"
    >
      <div class="panel-title">
        <div>
          <span class="kicker">TYphoon INTELLIGENCE</span>
          <h2>{{ panelMode === 'history' ? '历史路径' : panelMode === 'forecast' ? '模型预测' : panelMode === 'environment' ? 'ERA5 环境风场' : panelMode === 'risk' ? '风险研判' : '导出材料' }}</h2>
          <p class="panel-context">{{ selectedId }} · {{ selectedTyphoon?.name || selectedTyphoon?.ename || '未选择台风' }}</p>
        </div>
        <button class="close-button" type="button" title="隐藏分析面板" @click="showPanel = false">×</button>
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
          <span><i class="legend-swatch best-track"></i>蓝色：历史路径</span>
          <span><i class="legend-swatch replay-track"></i>蓝色：回放已观测段</span>
          <span><i class="legend-swatch observation-point"></i>灰色圆点：历史观测点</span>
          <span v-if="prediction"><i class="legend-swatch forecast-track"></i>青色虚线：CNN 预测路径</span>
          <span v-if="prediction"><i class="legend-swatch error-range"></i>橙色：历史校准误差范围</span>
          <span v-if="prediction"><i class="legend-swatch impact-range"></i>红色：模型估计影响范围</span>
        </div>
        <small class="panel-note">黄色圆点是真实观测位置；运行 CNN 预测后，地图会显示青色虚线和预测点。历史曲线只绘制数据中存在的字段。</small>
      </template>

      <template v-else-if="panelMode === 'forecast'">
        <div class="model-line">
          <span class="status-dot" :class="{ ready: modelReady }"></span>
          <span>{{ health ? (modelReady ? '轨迹 CNN 就绪 · ' + (health.model.device ?? '') : '模型不可用') : '服务状态检查中' }}</span>
        </div>

        <div class="forecast-subtabs" role="tablist" aria-label="模型预测子页面">
          <button type="button" :class="{ active: forecastSubpage === 'prediction' }" @click="forecastSubpage = 'prediction'">预测结果</button>
          <button type="button" :class="{ active: forecastSubpage === 'comparison' }" @click="forecastSubpage = 'comparison'">模型对比实验</button>
        </div>

        <template v-if="forecastSubpage === 'prediction'">

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
          <div><span>模型估计影响半径</span><strong>{{ selectedImpactRadiusKm?.toFixed(0) ?? '--' }} km</strong></div>
          <div v-if="selectedPoint.speed_interval_source"><span>联合风速区间</span><strong>{{ selectedPoint.speed_interval_source.lower.toFixed(1) }}–{{ selectedPoint.speed_interval_source.upper.toFixed(1) }} <small>源字段单位</small></strong></div>
          <div><span>90% 二维校准椭圆</span><strong>{{ selectedPoint.uncertainty_region?.semi_major_axis_km?.toFixed(0) ?? '--' }} × {{ selectedPoint.uncertainty_region?.semi_minor_axis_km?.toFixed(0) ?? '--' }} km · {{ selectedPoint.uncertainty_region?.bearing_deg?.toFixed(0) ?? '--' }}°</strong></div>
          <div><span>预测状态</span><strong>历史回放 / 模拟预测</strong></div>
        </div>

        <div class="prediction-meta">
          <div><span>模型版本</span><strong>{{ prediction?.model_version || health?.model.model_version || '--' }}</strong></div>
          <div><span>ERA5 匹配</span><strong>{{ era5Status }} · {{ era5?.matched_points ?? 0 }} 点</strong></div>
          <div><span>在线输入</span><strong>轨迹特征</strong></div>
          <div><span>误差单位</span><strong>球面距离 / km</strong></div>
          <div><span>数据来源</span><strong>CMA 历史记录 · ERA5 · CNN · 历史校准</strong></div>
          <div><span>影响估计依据</span><strong>CNN 风速 + 历史风圈 + 校准误差</strong></div>
        </div>
        <p v-if="prediction" class="impact-note">红色范围为启发式估计：位置校准半径 + 预测风速折算半径或最近历史风圈半径中的较大值；椭圆方向参考匹配的 ERA5 850 hPa 风向。ERA5 仅作环境背景，未作为当前在线 CNN 输入。该范围不是灾害概率、实际受灾范围或官方预警。</p>

        <p v-if="message" class="message">{{ message }}</p>
        <p v-else-if="!windowResult?.ok && selectedTyphoon" class="message">{{ windowResult?.reason }}</p>

        <button class="run-button" type="button" :disabled="loading || !selectedTyphoon || !canPredict || !modelReady" @click="runPrediction">
          {{ loading ? '计算中...' : prediction ? '重新预测' : '运行 CNN 预测' }}
        </button>
        </template>

        <template v-else>
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
        <button class="run-button" type="button" :disabled="!era5?.points.length" @click="exportCanvasPng('wind')">
          ⇩ 导出 ERA5 沿轨风矢量图 PNG
        </button>
        <small class="panel-note">当前数据只有沿台风中心轨迹匹配的 500/850 hPa u/v 样本，因此不显示没有数据支撑的完整区域风速图、湿度、海温、海平面气压或环境场剖面。</small>
      </template>

      <template v-else-if="panelMode === 'risk'">
        <div class="analysis-headline">
          <div><span>{{ analysisHorizon }}h 历史校准范围</span><strong>在线轨迹 CNN 的位置误差</strong></div>
          <span>{{ experimentSummary?.uncertainty.model_version || '--' }}</span>
        </div>
        <div class="analysis-horizons" role="group" aria-label="评估时效">
          <button v-for="hour in [6, 12, 18, 24, 30, 36]" :key="hour" type="button" :class="{ active: analysisHorizon === hour }" @click="analysisHorizon = hour">{{ hour }}h</button>
        </div>
        <div v-if="experimentSummary?.uncertainty.status === 'ready' && uncertaintyAt()" class="risk-summary">
          <div class="risk-radius"><span>90% 历史校准椭圆</span><strong>{{ uncertaintyAt()?.ellipse_90?.semi_major_axis_km?.toFixed(0) ?? '--' }} × {{ uncertaintyAt()?.ellipse_90?.semi_minor_axis_km?.toFixed(0) ?? '--' }} km</strong></div>
          <div class="analysis-metric-list">
            <div><span>椭圆面积</span><strong>{{ uncertaintyAt()?.ellipse_90?.area_km2?.toLocaleString(undefined, { maximumFractionDigits: 0 }) ?? '--' }} km²</strong></div>
            <div><span>方位角</span><strong>{{ uncertaintyAt()?.ellipse_90?.bearing_deg?.toFixed(1) ?? '--' }}°</strong></div>
            <div><span>窗口覆盖率（测试集）</span><strong>{{ formatPercent(uncertaintyAt()?.ellipse_90?.location_coverage_90) }}</strong></div>
            <div><span>整场台风覆盖率（测试集）</span><strong>{{ formatPercent(uncertaintyAt()?.ellipse_90?.location_storm_coverage_90) }}</strong></div>
            <div><span>位置 Energy Score</span><strong>{{ uncertaintyAt()?.energy_score_km?.toFixed(1) ?? '--' }} km</strong></div>
            <div><span>测试误差中位数</span><strong>{{ uncertaintyAt()?.median_error_km?.toFixed(1) ?? '--' }} km</strong></div>
            <div><span>校准目标</span><strong>{{ formatPercent(experimentSummary?.uncertainty.target_coverage) }}</strong></div>
          </div>
        </div>
        <div v-else class="message">{{ analysisError || '当前没有可用的历史校准范围。' }}</div>
        <div v-if="uncertaintyAt()" class="coverage-bar"><span :style="{ width: `${Math.min(100, (uncertaintyAt()?.location_coverage_90 ?? 0) * 100)}%` }"></span></div>
        <div class="risk-boundary">
          <strong>影响范围说明</strong>
          <span>红色为启发式影响范围估计，叠加历史校准误差与风圈参考；不代表实时灾害概率、实际受灾范围或官方预警。</span>
        </div>
        <button class="run-button" type="button" :disabled="!prediction" @click="downloadRiskGeoJson">
          ⇩ 导出校准误差与影响范围 GeoJSON
        </button>
        <small class="panel-note">数据来源：CMA 历史路径与强度、ERA5 850 hPa 沿轨风样本、在线轨迹 CNN 和历史校准记录。位置误差椭圆以 90% 为校准目标，但跨台风覆盖率未必达到目标；红色影响范围是启发式估计，不是灾害概率区。</small>
      </template>

      <template v-else>
        <div class="section-heading"><span>导出台风分析材料</span><small>{{ selectedId }}</small></div>
        <details class="export-legend-picker">
          <summary class="export-legend-trigger">
            <strong>选择面板显示内容</strong>
            <span>{{ exportLegendIds.length + exportInfoIds.length }} / {{ EXPORT_LEGEND_OPTIONS.length + EXPORT_INFO_OPTIONS.length }} 已选</span>
          </summary>
          <div class="export-legend-menu">
            <div class="export-legend-actions">
              <button type="button" @click="setAllExportContent(true)">全选</button>
              <button type="button" @click="setAllExportContent(false)">清空</button>
            </div>
            <strong class="export-option-group-title">图例</strong>
            <label v-for="item in EXPORT_LEGEND_OPTIONS" :key="item.id" class="export-legend-option">
              <input type="checkbox" :checked="exportLegendIds.includes(item.id)" @change="setExportLegendEnabled(item.id, ($event.target as HTMLInputElement).checked)" />
              <i class="legend-swatch" :class="item.id === 'error' ? 'error-range' : item.id === 'impact' ? 'impact-range' : item.id === 'prediction' ? 'forecast-track' : 'best-track'"></i>
              <span>{{ item.label }}</span>
            </label>
            <strong class="export-option-group-title">信息内容</strong>
            <label v-for="item in EXPORT_INFO_OPTIONS" :key="item.id" class="export-legend-option">
              <input type="checkbox" :checked="exportInfoIds.includes(item.id)" @change="setExportInfoEnabled(item.id, ($event.target as HTMLInputElement).checked)" />
              <span>{{ item.label }}</span>
            </label>
            <small>信息内容控制面板文字行的显隐；设置统一应用于回放视频、三维截图和 ERA5 风场图。</small>
          </div>
        </details>
        <button class="run-button" type="button" :disabled="!selectedTyphoon" @click="recordReplayVideo">
          {{ recording ? '停止录制并下载' : '录制历史回放视频' }}
        </button>
        <button class="run-button secondary-run-button" type="button" :disabled="!selectedTyphoon" @click="exportCanvasPng('screenshot')">
          导出三维视图 PNG
        </button>
        <button class="run-button secondary-run-button" type="button" :disabled="!era5?.points.length" @click="exportCanvasPng('wind')">
          导出 ERA5 沿轨风矢量图 PNG
        </button>
        <button class="run-button secondary-run-button" type="button" :disabled="!prediction" @click="downloadRiskGeoJson">
          导出预测误差与影响范围 GeoJSON
        </button>
        <button class="run-button secondary-run-button" type="button" :disabled="!selectedTyphoon" @click="downloadTrack">
          导出台风分析数据 JSON
        </button>
        <div class="risk-boundary">
          <strong>导出说明</strong>
          <span>支持 MP4 的浏览器导出 MP4，否则会保存为 WebM。ERA5 图片展示沿台风中心匹配的 850 hPa 风矢量，不是连续网格风场。</span>
        </div>
        <small class="panel-note">PNG 导出当前三维地图画布；风矢量 PNG 还需所选台风有可用 ERA5 匹配样本。GeoJSON 分开标注历史校准误差椭圆与启发式影响范围。</small>
      </template>
    </section>

    <footer
      v-if="selectedTyphoon"
      ref="timelineElement"
      class="legacy-timeline"
    >
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
  top: 8px;
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

.screen-brand h1 {
  margin: 0 0 3px;
  color: #f2ffff;
  font-size: 25px;
  font-weight: 600;
  line-height: 1.2;
  white-space: nowrap;
}

.screen-brand-title-row {
  display: flex;
  flex-wrap: nowrap;
  align-items: baseline;
  column-gap: 16px;
}

.screen-brand-subtitle {
  display: block;
  color: #ffffff;
  font-size: 11px;
  white-space: nowrap;
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
  box-sizing: border-box;
  resize: none;
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

.metric-switch button {
  min-height: 28px;
  border: 0;
  border-radius: 2px;
  color: #91afb3;
  background: transparent;
  cursor: pointer;
  font-size: 11px;
}

.metric-switch button.active {
  color: #06212a;
  background: #67e8d0;
}

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
  background: #4da3ff;
}

.legend-swatch.replay-track,
.legend-swatch.forecast-track {
  background: #4da3ff;
}

.legend-swatch.observation-point {
  width: 9px;
  height: 9px;
  border: 2px solid #102431;
  border-radius: 50%;
  background: #b8c0c5;
}

.legend-swatch.forecast-track {
  background: repeating-linear-gradient(90deg, #67e8d0 0 5px, transparent 5px 8px);
}

.legend-swatch.impact-range {
  height: 9px;
  border: 1px solid #ef4444;
  background: rgba(239, 68, 68, 0.18);
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

.export-legend-picker {
  margin: 10px 0 12px;
  border: 1px solid rgba(143, 207, 205, 0.22);
  background: rgba(8, 36, 48, 0.58);
}

.export-legend-trigger,
.export-legend-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.export-legend-trigger {
  min-height: 38px;
  padding: 0 10px;
  color: #dff8f4;
  font-size: 11px;
  cursor: pointer;
  list-style: none;
}

.export-legend-trigger::-webkit-details-marker {
  display: none;
}

.export-legend-trigger::after {
  content: '⌄';
  margin-left: auto;
  color: #9fe8dc;
  font-size: 15px;
  line-height: 1;
}

.export-legend-picker[open] .export-legend-trigger::after {
  content: '⌃';
}

.export-legend-trigger span,
.export-legend-picker small {
  color: #8eabb0;
  font-size: 10px;
}

.export-legend-menu {
  padding: 0 10px 9px;
  border-top: 1px solid rgba(143, 207, 205, 0.16);
}

.export-legend-actions {
  justify-content: flex-start;
  margin: 7px 0 3px;
}

.export-legend-actions button {
  padding: 2px 7px;
  border: 1px solid rgba(103, 232, 208, 0.35);
  color: #9fe8dc;
  background: transparent;
  font-size: 10px;
  cursor: pointer;
}

.export-option-group-title {
  display: block;
  margin: 8px 0 2px;
  color: #9fe8dc;
  font-size: 10px;
  font-weight: 600;
}

.export-legend-option {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 24px;
  color: #d5e9ee;
  font-size: 11px;
  cursor: pointer;
}

.export-legend-option input {
  accent-color: #67e8d0;
}

.export-legend-option .legend-swatch {
  flex: 0 0 auto;
  margin-right: 0;
}

.export-legend-picker small {
  display: block;
  margin-top: 5px;
  line-height: 1.4;
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

.forecast-subtabs {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px;
  margin: 0 0 14px;
  padding: 3px;
  border: 1px solid rgba(143, 207, 205, 0.2);
  background: rgba(8, 36, 48, 0.7);
}

.forecast-subtabs button {
  min-height: 30px;
  border: 0;
  border-radius: 2px;
  color: #9dbabc;
  background: transparent;
  cursor: pointer;
  font-size: 11px;
}

.forecast-subtabs button.active {
  color: #06212a;
  background: #67e8d0;
  font-weight: 700;
}

.impact-note {
  margin: 9px 0 0;
  padding-left: 9px;
  border-left: 2px solid #ef4444;
  color: #a9c0c4;
  font-size: 10px;
  line-height: 1.55;
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
  box-sizing: border-box;
  resize: none;
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

  .screen-brand-title-row {
    flex-wrap: wrap;
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

@media (max-width: 1100px) and (min-width: 761px) {
  .screen-brand-title-row {
    flex-wrap: wrap;
  }
}
</style>
