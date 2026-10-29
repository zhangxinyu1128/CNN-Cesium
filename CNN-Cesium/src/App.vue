<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import WorkspaceApp from './App.workspace.vue'
import AssistantPanel from '@/components/AssistantPanel.vue'
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
  AssistantContext,
  AssistantHistoricalCase,
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
const assistantOpen = ref(false)
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
let legacyHeaderResizeObserver: ResizeObserver | null = null
const positionReadout = ref({ longitude: '--', latitude: '--', altitude: '--' })

function syncLegacyPositionReadout(doc = iframeRef.value?.contentDocument) {
  const position = doc?.querySelector<HTMLElement>('.position-container')
  if (!position) return
  const values = Array.from(position.children).map((item) => item.textContent?.trim() || '--')
  if (values.length >= 3) {
    positionReadout.value = {
      longitude: values[0],
      latitude: values[1],
      altitude: values[2]
    }
  }
  position.style.display = 'none'
}

function applyLegacyListLayout(doc = iframeRef.value?.contentDocument) {
  if (!doc) return
  const style = doc.getElementById('main-screen-overrides') as HTMLStyleElement | null
  if (!style) return
  style.textContent = `
    .layout .layout-header { display: none !important; }
    .layout .layout-footer { display: none !important; }
    html.main-screen-embedded .layout-main .list-panel .widget-panel {
      top: 0 !important;
      left: 4px !important;
      right: auto !important;
      bottom: 0 !important;
      width: min(24vw, 420px) !important;
      height: auto !important;
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

function handleLegacyViewportResize() {
  applyLegacyListLayout()
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
type LegacyHistoricalOverlay = {
  entities: LegacyEntity[]
}

const legacyHighlights = new Map<string, LegacyHighlight>()
let legacyPredictionOverlay: LegacyPredictionOverlay | null = null
let legacyEra5Overlay: LegacyPredictionOverlay | null = null
let legacyHistoricalOverlay: LegacyHistoricalOverlay | null = null
const highlightColors = ['#ff1493', '#00fff0', '#ffe600', '#76ff03', '#ff6d00']
const intensityColors: Record<string, string> = {
  '热带低压(TD)': '#30fc31',
  '热带风暴(TS)': '#307efa',
  '强热带风暴(STS)': '#fffc00',
  '台风(TY)': '#ff9c00',
  '强台风(STY)': '#fb7cff',
  '超强台风(Super TY)': '#fa3030'
}
const intensityLegend = Object.entries(intensityColors).map(([label, color]) => ({
  label: label.replace(/\(.*\)$/, ''),
  color
}))
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
let historyTableRaf: number | undefined
let historyTableLastFrame = 0
const replayStepMs = 500
const historyTableScrollStep = 34
const historyTableRowsAbove = 2
const historyTableBody = ref<HTMLTableSectionElement | null>(null)
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
const modelInputSteps = computed(() => health.value?.model.input_steps ?? 4)
const windowResult = computed(() =>
  selectedTyphoon.value
    ? buildPredictionWindow(
      selectedTyphoon.value.points,
      selectedTyphoon.value.points.length - 1,
      modelInputSteps.value,
      health.value?.model.coordinate_system === 'local_motion_v1'
    )
    : null
)
const canPredict = computed(() => Boolean(windowResult.value?.ok))
const currentPoint = computed(() => selectedTyphoon.value?.points[currentIndex.value] ?? null)
const historyTableRows = computed(() => {
  const points = selectedTyphoon.value?.points ?? []
  return points.map((point, pointIndex) => ({ point, pointIndex }))
})
const replayProgress = computed(() => {
  const length = selectedTyphoon.value?.points.length ?? 0
  return length > 1 ? currentIndex.value / (length - 1) : 0
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
const assistantContext = computed<AssistantContext>(() => {
  const point = selectedPoint.value
  const region = point?.uncertainty_region
  const wind = forecastEra5Point.value?.levels['850']
  return {
    typhoon_id: selectedTyphoon.value?.tfbh,
    typhoon_name: selectedTyphoon.value?.name || selectedTyphoon.value?.ename,
    lead_hours: selectedLead.value,
    forecast_position: point ? { lng: point.lng, lat: point.lat } : undefined,
    speed_ms: point?.speed_ms ?? undefined,
    location_radius_90_km:
      point?.location_radius_90_km ??
      experimentSummary.value?.uncertainty?.by_horizon?.find((item) => item.lead_hours === selectedLead.value)?.location_radius_90_km ??
      undefined,
    uncertainty_region: region
      ? {
        semi_major_axis_km: region.semi_major_axis_km,
        semi_minor_axis_km: region.semi_minor_axis_km,
        coverage: region.coverage
      }
      : undefined,
    era5: wind
      ? {
        level: 850,
        u: wind.u,
        v: wind.v,
        speed_ms: wind.speed_ms,
        direction_deg: wind.direction_deg,
        shear: forecastEra5Point.value?.shear_500_850_ms ?? undefined
      }
      : undefined,
    current_observation: currentPoint.value
      ? {
        time: currentPoint.value.time,
        lng: currentPoint.value.lng,
        lat: currentPoint.value.lat,
        power: currentPoint.value.power,
        speed: currentPoint.value.speed,
        pressure: currentPoint.value.pressure,
        strong: currentPoint.value.strong
      }
      : undefined,
    observed_points: selectedTyphoon.value
      ? selectedTyphoon.value.points
        .slice(Math.max(0, currentIndex.value - 7), currentIndex.value + 1)
        .map(({ time, lng, lat, power, speed, strong }) => ({ time, lng, lat, power, speed, strong }))
      : [],
    forecast_points: prediction.value?.predictions.map((forecast) => ({
      lead_hours: forecast.lead_hours,
      lng: forecast.lng,
      lat: forecast.lat,
      speed_ms: forecast.speed_ms,
      location_radius_90_km: forecast.location_radius_90_km
    })) ?? []
  }
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
      width: 7,
      clampToGround: true,
      material
    }
  })
  const observationPoints = points.map((point, index) => viewer.entities.add({
    id: `MainObservation-${detail.tfbh}-${index}`,
    name: `${detail.name || detail.ename || detail.tfbh} 观测点 ${index + 1} · ${point.time}`,
    position: Cesium.Cartesian3.fromDegrees(point.lng, point.lat, 18000),
    point: {
      pixelSize: 13,
      color: Cesium.Color.fromCssColorString(intensityColors[point.strong ?? ''] ?? '#b8c0c5'),
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
    point: { pixelSize: 18, color: lineColor, outlineColor: white, outlineWidth: 3 }
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
      width: 9,
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

function clearLegacyHistoricalOverlay() {
  const viewer = legacyFrame()?.viewer
  if (viewer && legacyHistoricalOverlay) {
    legacyHistoricalOverlay.entities.forEach((entity) => viewer.entities.remove(entity))
  }
  legacyHistoricalOverlay = null
}

function showHistoricalCase(historicalCase: AssistantHistoricalCase) {
  clearLegacyHistoricalOverlay()
  const frame = legacyFrame()
  const Cesium = frame?.Cesium
  const viewer = frame?.viewer
  const points = historicalCase.points.filter(
    (point) => Number.isFinite(point.lng) && Number.isFinite(point.lat)
  )
  if (!Cesium || !viewer || points.length < 2) {
    message.value = '历史参考路径数据不足，无法叠加到地图'
    return
  }

  const color = Cesium.Color.fromCssColorString('#ffc66d')
  const dashed = Cesium.PolylineDashMaterialProperty
    ? new Cesium.PolylineDashMaterialProperty({ color, dashLength: 12 })
    : color
  const entities: LegacyEntity[] = [viewer.entities.add({
    id: `AssistantHistoricalCase-${historicalCase.tfbh}`,
    name: `${historicalCase.year} ${historicalCase.name} 历史相似案例路径`,
    polyline: {
      positions: Cesium.Cartesian3.fromDegreesArray(points.flatMap((point) => [point.lng, point.lat])),
      width: 5,
      clampToGround: true,
      material: dashed
    }
  })]
  const first = points[0]
  const last = points[points.length - 1]
  entities.push(viewer.entities.add({
    id: `AssistantHistoricalCaseStart-${historicalCase.tfbh}`,
    name: `${historicalCase.name} 匹配参考段起点`,
    position: Cesium.Cartesian3.fromDegrees(first.lng, first.lat, 14000),
    point: { pixelSize: 10, color, outlineColor: Cesium.Color.fromCssColorString('#102431'), outlineWidth: 2 }
  }))
  entities.push(viewer.entities.add({
    id: `AssistantHistoricalCaseEnd-${historicalCase.tfbh}`,
    name: `${historicalCase.name} 历史路径终点`,
    position: Cesium.Cartesian3.fromDegrees(last.lng, last.lat, 14000),
    point: { pixelSize: 12, color, outlineColor: Cesium.Color.fromCssColorString('#ffffff'), outlineWidth: 2 }
  }))
  legacyHistoricalOverlay = { entities }
  viewer.scene?.requestRender?.()
  void viewer.flyTo(entities, {
    duration: 1.0,
    offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-66), 0)
  })
  message.value = `已叠加 ${historicalCase.year} 年 ${historicalCase.name} 的历史参考路径；该路径不代表当前预测`
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

function legacyRowTfbh(row: HTMLTableRowElement) {
  // The legacy table renders its cells without separators, so a row reads like
  // "202501WUTIPWUTIP" — no word boundary follows the digits. Read the id cell
  // directly instead of regexing the whole row text.
  for (const cell of row.querySelectorAll('td')) {
    const text = cell.textContent?.trim() ?? ''
    if (/^\d{4,8}$/.test(text)) return text
  }
  return undefined
}

function checkedLegacyIds() {
  const doc = iframeRef.value?.contentDocument
  if (!doc) return []
  return Array.from(doc.querySelectorAll('tr')).flatMap((row) => {
    const checkbox = row.querySelector<HTMLInputElement>('input[type="checkbox"]')
    const id = legacyRowTfbh(row)
    return checkbox?.checked && id ? [id] : []
  })
}

function legacyRowId(row: HTMLTableRowElement) {
  return legacyRowTfbh(row)
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

  const id = legacyRowTfbh(row)
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
  syncLegacyPositionReadout(doc)
  legacyHeaderResizeObserver?.disconnect()
  const header = iframeRef.value?.ownerDocument.querySelector<HTMLElement>('.screen-header')
  if (header) {
    legacyHeaderResizeObserver = new ResizeObserver(() => applyLegacyListLayout(doc))
    legacyHeaderResizeObserver.observe(header)
  }
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
      syncLegacyPositionReadout(doc)
      requestLegacyHighlightSync()
    })
    legacyObserver.observe(doc.body, {
      subtree: true,
      childList: true,
      characterData: true,
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

function stopHistoryTableScroll() {
  if (historyTableRaf !== undefined) {
    window.cancelAnimationFrame(historyTableRaf)
    historyTableRaf = undefined
  }
}

function historyTableScrollWrap() {
  return historyTableBody.value?.closest<HTMLElement>('.history-data-table-wrap') ?? null
}

const historyTableCollapsed = ref(false)
let ignoreTableHover = false

function toggleHistoryTable() {
  historyTableCollapsed.value = !historyTableCollapsed.value
  if (historyTableCollapsed.value) {
    ignoreTableHover = false
    stopHistoryTableScroll()
  } else {
    ignoreTableHover = true
    startHistoryTableScroll()
  }
}

function pauseHistoryTableScroll() {
  if (ignoreTableHover) return
  stopHistoryTableScroll()
}

function resumeHistoryTableScroll() {
  ignoreTableHover = false
  startHistoryTableScroll()
}

function startHistoryTableScroll(reset = false) {
  stopHistoryTableScroll()
  const tableWrap = historyTableScrollWrap()
  if (!tableWrap) return
  if (reset) tableWrap.scrollTop = 0
  historyTableLastFrame = 0
  const step = (timestamp: number) => {
    const wrap = historyTableScrollWrap()
    if (!wrap) return
    const maxScrollTop = wrap.scrollHeight - wrap.clientHeight
    const offset = (currentIndex.value - historyTableRowsAbove) * historyTableScrollStep
    const target = Math.min(Math.max(0, offset), Math.max(0, maxScrollTop))
    const distance = target - wrap.scrollTop
    if (Math.abs(distance) < 0.5 || Math.abs(distance) > historyTableScrollStep * 4) {
      wrap.scrollTop = target
    } else {
      const delta = historyTableLastFrame ? Math.min(timestamp - historyTableLastFrame, 64) : 16.7
      wrap.scrollTop += distance * (1 - Math.exp(-delta / 80))
    }
    historyTableLastFrame = timestamp
    historyTableRaf = window.requestAnimationFrame(step)
  }
  historyTableRaf = window.requestAnimationFrame(step)
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
    const viewer = legacyFrame()?.viewer as (LegacyViewer & { render?: () => void }) | undefined
    if (viewer && typeof viewer.render === 'function') {
      viewer.render()
    }
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
    const arrowX = left + 10 * scale
    const arrowY = y - 5 * scale
    const angle = Math.atan2(wind.v, wind.u)
    // Keep each arrow inside its own row band (lineHeight). The old 84px shaft
    // ran through several rows and the arrows overlapped into a clump.
    const arrowLength = (10 + Math.min(Math.max(wind.speed_ms, 0), 30) * 0.25) * scale
    const headLength = 5.5 * scale
    context.strokeStyle = '#b8c0c5'
    context.fillStyle = '#b8c0c5'
    context.lineWidth = Math.max(1, 2 * scale)
    context.beginPath()
    context.moveTo(arrowX, arrowY)
    context.lineTo(arrowX + Math.cos(angle) * arrowLength, arrowY - Math.sin(angle) * arrowLength)
    context.stroke()
    context.beginPath()
    context.moveTo(arrowX + Math.cos(angle) * arrowLength, arrowY - Math.sin(angle) * arrowLength)
    context.lineTo(arrowX + Math.cos(angle + 2.55) * headLength, arrowY - Math.sin(angle + 2.55) * headLength)
    context.lineTo(arrowX + Math.cos(angle - 2.55) * headLength, arrowY - Math.sin(angle - 2.55) * headLength)
    context.closePath()
    context.fill()
    context.fillStyle = '#e5f1f3'
    context.font = `500 ${12 * scale}px "Microsoft YaHei", Arial, sans-serif`
    const time = point.time.replace('T', ' ').slice(0, 16)
    context.fillText(`${time}   ${wind.speed_ms.toFixed(1)} m/s   ${wind.direction_deg.toFixed(0)}°`, left + 46 * scale, y)
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

watch(selectedId, () => clearLegacyHistoricalOverlay())

watch(selectedTyphoon, async () => {
  await nextTick()
  startHistoryTableScroll(true)
  window.setTimeout(() => startHistoryTableScroll(), 240)
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
  stopHistoryTableScroll()
  finishReplayRecording()
  clearLegacyPrediction()
  clearLegacyEra5()
  clearLegacyHistoricalOverlay()
  legacyDblClickDocument?.removeEventListener('dblclick', handleLegacyTyphoonDblClick, true)
  legacyDblClickWindow?.removeEventListener('dblclick', handleLegacyTyphoonDblClick, true)
  legacyCheckboxDocument?.removeEventListener('change', handleLegacyCheckboxChange, true)
  legacyObserver?.disconnect()
  legacyHeaderResizeObserver?.disconnect()
  window.removeEventListener('resize', handleLegacyViewportResize)
  if (legacySyncTimer) window.clearTimeout(legacySyncTimer)
  if (exportSettingsSaveTimer) window.clearTimeout(exportSettingsSaveTimer)
})

onMounted(async () => {
  window.addEventListener('resize', handleLegacyViewportResize)
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
        <span class="screen-brand-mark" aria-hidden="true">
          <svg viewBox="0 0 44 44" role="presentation">
            <circle cx="22" cy="22" r="17" />
            <path d="M14 18c2-5 9-7 14-3 3 2 4 5 3 8M30 26c-2 5-9 7-14 3-3-2-4-5-3-8" />
            <circle cx="22" cy="22" r="2.3" class="mark-core" />
          </svg>
        </span>
        <div class="screen-brand-copy">
          <h1>热带气旋可视化与预测平台</h1>
          <span class="screen-brand-subtitle">驭风智瞳 <i>|</i> 气旋视界队</span>
        </div>
      </div>

      <div class="screen-header-status" aria-label="系统状态">
        <span class="status-connector" aria-hidden="true"><i></i></span>
        <span class="status-dot" :class="{ ready: health?.data.status === 'ready' }"></span>
        <span>在线</span>
      </div>

      <nav class="top-actions" aria-label="功能模块">
        <button class="nav-item" type="button" :class="{ active: !showPanel }" title="回到大屏全局视角" @click="resetGlobal">
          <svg viewBox="0 0 28 28" aria-hidden="true"><path d="M5 13.5 14 6l9 7.5M8 12.5V22h12v-9.5M11 22v-6h6v6" /></svg>
          <span>全局视角<small>Overview</small></span>
        </button>
        <button class="nav-item" type="button" :class="{ active: showPanel && panelMode === 'history' }" title="打开历史路径面板" @click="openPanel('history')">
          <svg viewBox="0 0 28 28" aria-hidden="true"><circle cx="14" cy="14" r="7.5" /><path d="M14 9.5v4.8l3.5 2.2" /></svg>
          <span>历史路径<small>History</small></span>
        </button>
        <button class="nav-item" type="button" :class="{ active: showPanel && panelMode === 'forecast' }" title="打开模型预测面板" @click="openPanel('forecast')">
          <svg viewBox="0 0 28 28" aria-hidden="true"><path d="m5 20 6-8 5 4 7-10" /><path d="M18 6h5v5" /></svg>
          <span>模型预测<small>Prediction</small></span>
        </button>
        <button class="nav-item" type="button" :class="{ active: showPanel && panelMode === 'environment' }" title="打开 ERA5 环境场面板" @click="openPanel('environment')">
          <svg viewBox="0 0 28 28" aria-hidden="true"><path d="M5 9h14M4 14h18M7 19h10" /><path d="M19 17a3 3 0 1 0 0-6" /></svg>
          <span>环境风场<small>Wind Field</small></span>
        </button>
        <button class="nav-item" type="button" :class="{ active: showPanel && panelMode === 'risk' }" title="打开风险研判面板" @click="openPanel('risk')">
          <svg viewBox="0 0 28 28" aria-hidden="true"><path d="M14 4 23 7.5v6.2c0 5-3.8 8-9 10-5.2-2-9-5-9-10V7.5L14 4Z" /><path d="m10 14 2.7 2.7L18 11" /></svg>
          <span>风险研判<small>Risk</small></span>
        </button>
        <button class="nav-item" type="button" :class="{ active: assistantOpen }" title="打开 AI 研判助手" @click="assistantOpen = !assistantOpen">
          <svg viewBox="0 0 28 28" aria-hidden="true"><path d="m14 3 2.5 7.5L24 13l-7.5 2.5L14 23l-2.5-7.5L4 13l7.5-2.5L14 3Z" /></svg>
          <span>AI 研判<small>Assistant</small></span>
        </button>
      </nav>

      <button class="runtime-status" type="button" title="查看运行状态" @click="openPanel('forecast')">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 2-6 11h5l-2 9 6-11h-5l2-9Z" /></svg>
        <span>运行状态</span>
      </button>
      <button class="export-action" type="button" :class="{ active: showPanel && panelMode === 'export' }" title="打开导出面板" @click="openPanel('export')">
        <svg viewBox="0 0 28 28" aria-hidden="true"><path d="M14 19V7M9 12l5-5 5 5M7 20c1.5 3 12.5 3 14 0" /></svg>
        <span>导出</span>
      </button>
    </header>

    <section
      v-if="showPanel"
      ref="panelElement"
      class="forecast-panel analysis-panel"
      :class="{ 'analysis-panel-export': panelMode === 'export' }"
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

    <section
      v-if="selectedTyphoon"
      class="history-data-panel"
      :class="{ 'is-collapsed': historyTableCollapsed }"
      aria-label="历史路径数据展示"
    >
      <div class="history-data-heading">
        <div>
          <span class="history-data-title">历史路径数据展示</span>
          <small>{{ selectedTyphoon.name || selectedTyphoon.ename || selectedTyphoon.tfbh }} · {{ selectedTyphoon.points.length }} 个观测点</small>
          <ul class="history-intensity-legend" aria-label="台风强度图例">
            <li v-for="item in intensityLegend" :key="item.label">
              <i :style="{ background: item.color }"></i>{{ item.label }}
            </li>
          </ul>
        </div>
        <div class="history-data-actions">
          <span class="history-data-live"><i></i>自动滚动</span>
          <button
            class="history-data-toggle"
            type="button"
            :aria-expanded="!historyTableCollapsed"
            :title="historyTableCollapsed ? '展开历史路径数据' : '收起历史路径数据'"
            @click="toggleHistoryTable"
          >
            {{ historyTableCollapsed ? '展开' : '收起' }}
          </button>
        </div>
      </div>
      <div
        v-show="!historyTableCollapsed"
        class="history-data-table-wrap"
        @mouseenter="pauseHistoryTableScroll"
        @mouseleave="resumeHistoryTableScroll"
      >
        <table class="history-data-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>经度 (°E)</th>
              <th>纬度 (°N)</th>
              <th>风速 (m/s)</th>
              <th>气压 (hPa)</th>
              <th>强度</th>
            </tr>
          </thead>
          <tbody ref="historyTableBody">
            <tr
              v-for="row in historyTableRows"
              :key="`${row.pointIndex}-${row.point.time}`"
              :class="{ current: row.pointIndex === currentIndex }"
            >
              <td>{{ row.point.time?.replace('T', ' ') ?? '--' }}</td>
              <td>{{ Number.isFinite(row.point.lng) ? row.point.lng.toFixed(1) : '--' }}</td>
              <td>{{ Number.isFinite(row.point.lat) ? row.point.lat.toFixed(1) : '--' }}</td>
              <td class="accent-speed">{{ typeof row.point.speed === 'number' ? row.point.speed.toFixed(1) : '--' }}</td>
              <td class="accent-pressure">{{ typeof row.point.pressure === 'number' ? row.point.pressure.toFixed(0) : '--' }}</td>
              <td>{{ row.point.strong || '--' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <footer
      ref="timelineElement"
      class="legacy-timeline"
      :class="{ 'is-empty': !selectedTyphoon }"
    >
      <div class="legacy-timeline-control">
        <button
          class="legacy-play-button"
          type="button"
          :disabled="!selectedTyphoon?.points.length"
          :title="playing ? '暂停历史回顾' : '播放历史回顾'"
          @click="toggleReplay"
        >
          {{ playing ? 'Ⅱ' : '▶' }}
        </button>
        <div>
          <strong>历史回顾</strong>
          <small>{{ selectedTyphoon ? `${currentIndex + 1} / ${selectedTyphoon.points.length} 个观测点` : '等待历史数据' }}</small>
        </div>
      </div>
      <div class="legacy-timeline-track" :style="{ '--progress': replayProgress }">
        <input
          v-model.number="currentIndex"
          type="range"
          min="0"
          :max="Math.max((selectedTyphoon?.points.length ?? 0) - 1, 0)"
          :disabled="!selectedTyphoon?.points.length"
          aria-label="历史回顾进度"
        />
      </div>
      <div class="legacy-replay-readout">
        <strong>{{ selectedTyphoon ? currentPoint?.time?.replace('T', ' ') ?? '--' : '当前状态' }}</strong>
        <span>{{ selectedTyphoon ? `${currentPoint?.lat.toFixed(1) ?? '--'}°N · ${currentPoint?.lng.toFixed(1) ?? '--'}°E` : '尚无可回放的观测点' }}</span>
      </div>
      <button class="legacy-reset-button" type="button" :disabled="!selectedTyphoon?.points.length" @click="resetTimeline">重置</button>
    </footer>
    <div class="viewport-readout" aria-label="地图视角位置">
      <span>{{ positionReadout.longitude }}</span>
      <span>{{ positionReadout.latitude }}</span>
      <span>{{ positionReadout.altitude }}</span>
    </div>
    <AssistantPanel
      v-if="assistantOpen"
      :open="assistantOpen"
      :context="assistantContext"
      @close="assistantOpen = false"
      @show-historical-case="showHistoricalCase"
    />
  </main>
</template>

<style scoped>
.screen {
  position: relative;
  display: grid;
  grid-template-rows: 108px minmax(0, 1fr) 96px 42px;
  width: 100%;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  background: #030b14;
}

.dashboard {
  display: block;
  width: 100%;
  height: 100%;
  min-height: 0;
  border: 0;
  grid-row: 2;
}

.screen-header {
  position: relative;
  grid-row: 1;
  min-width: 0;
  height: 108px;
  z-index: 40;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 0 22px;
  border-bottom: 1px solid rgba(33, 82, 117, 0.72);
  background: linear-gradient(180deg, #0e2238 0%, #0a1930 100%);
  box-shadow: 0 3px 18px rgba(0, 0, 0, 0.28);
  box-sizing: border-box;
  pointer-events: none;
}

.screen-brand {
  display: flex;
  flex: 0 0 clamp(492px, 29.7vw, 758px);
  align-items: center;
  min-width: 0;
  gap: 11px;
  color: #e4ffff;
  pointer-events: auto;
}

.screen-brand-mark {
  display: grid;
  flex: 0 0 50px;
  width: 50px;
  height: 50px;
  place-items: center;
  border: 1px solid #3ec9e8;
  border-radius: 50%;
  color: #5fe0f5;
  background: #0d3450;
  box-shadow: 0 0 13px rgba(62, 201, 232, 0.72), inset 0 0 10px rgba(62, 201, 232, 0.16);
}

.screen-brand-mark svg {
  width: 38px;
  height: 38px;
  fill: none;
  stroke: currentColor;
  stroke-linecap: round;
  stroke-width: 1.8;
  filter: drop-shadow(0 0 4px rgba(95, 224, 245, 0.7));
}

.screen-brand-mark .mark-core {
  fill: #8fefff;
  stroke: none;
}

.screen-brand-copy {
  min-width: 0;
}

.screen-brand h1 {
  overflow: hidden;
  margin: 0;
  color: #62d0ef;
  font-size: clamp(22.8px, 2.4vw, 33.6px);
  font-weight: 700;
  line-height: 1.08;
  letter-spacing: 2px;
  text-shadow: 0 0 8px rgba(79, 198, 238, 0.44);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.screen-brand-subtitle {
  display: block;
  margin-top: 5px;
  color: #7fc3de;
  font-size: 15px;
  letter-spacing: 1px;
  white-space: nowrap;
}

.screen-brand-subtitle i {
  margin: 0 6px;
  color: #4e89a8;
  font-style: normal;
}

.screen-header-status {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 7px;
  color: #3ddc84;
  font-size: 19px;
  letter-spacing: 1px;
  pointer-events: auto;
}

.status-connector {
  position: relative;
  display: block;
  width: 40px;
  height: 12px;
  margin-right: 1px;
}

.status-connector::before,
.status-connector::after {
  position: absolute;
  top: 5px;
  height: 1px;
  content: '';
  background: #2aa8cc;
}

.status-connector::before {
  right: 16px;
  left: 0;
}

.status-connector::after {
  right: 0;
  width: 10px;
}

.status-connector i {
  position: absolute;
  top: 1px;
  right: 10px;
  width: 8px;
  height: 8px;
  border: 1px solid #3ec9e8;
  border-radius: 50%;
}

.status-connector i::after {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 2px;
  height: 2px;
  border-radius: 50%;
  content: '';
  background: #7fe8ff;
}

.top-actions {
  display: flex;
  flex: 0 1 auto;
  min-width: 0;
  height: 60px;
  overflow: hidden;
  border: 1px solid #1d4a6b;
  border-radius: 29px;
  background: #10283f;
  pointer-events: auto;
}

.nav-item {
  display: flex;
  flex: 1 1 94px;
  align-items: center;
  justify-content: center;
  min-width: 78px;
  height: 58px;
  gap: 5px;
  padding: 5px 10px;
  border: 0;
  border-radius: 26px;
  color: #bfe3f2;
  background: transparent;
  cursor: pointer;
  font: 500 16px/1.1 'Microsoft YaHei', sans-serif;
  letter-spacing: 0.5px;
  transition: color 160ms ease, background 160ms ease, box-shadow 160ms ease;
}

.nav-item svg {
  flex: 0 0 25px;
  width: 25px;
  height: 25px;
  fill: none;
  stroke: #5fc8e6;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.8;
}

.nav-item span {
  display: grid;
  gap: 3px;
  text-align: center;
  white-space: nowrap;
}

.nav-item small {
  color: #5e93b2;
  font-size: 12px;
  letter-spacing: 0;
}

.nav-item:hover,
.nav-item.active {
  color: #fff;
  background: linear-gradient(180deg, #25b7d6, #1490b4);
  box-shadow: 0 0 15px rgba(39, 195, 224, 0.56);
}

.nav-item.active svg,
.nav-item:hover svg {
  stroke: #fff;
}

.nav-item.active small,
.nav-item:hover small {
  color: #d8f4ff;
}

.runtime-status {
  display: grid;
  flex: 0 0 58px;
  justify-items: center;
  gap: 1px;
  padding: 0;
  color: #bfe3f2;
  background: transparent;
  cursor: pointer;
  font: 500 12px/1.1 'Microsoft YaHei', sans-serif;
  letter-spacing: 0.7px;
  pointer-events: auto;
}

.runtime-status svg {
  width: 25px;
  height: 25px;
  fill: none;
  stroke: #f2c94c;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.8;
  filter: drop-shadow(0 0 4px rgba(242, 201, 76, 0.42));
}

.runtime-status:hover {
  color: #fff;
}

.export-action {
  display: flex;
  flex: 0 0 78px;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 0;
  color: #bfe3f2;
  background: transparent;
  cursor: pointer;
  font: 500 15px/1.1 'Microsoft YaHei', sans-serif;
  letter-spacing: 1px;
  pointer-events: auto;
}

.export-action svg {
  width: 38px;
  height: 38px;
  padding: 7px;
  border: 1.5px solid #3ec9e8;
  border-radius: 50%;
  fill: none;
  stroke: #8fe8ff;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.9;
  background: #12314c;
  box-shadow: 0 0 12px rgba(62, 201, 232, 0.46);
}

.export-action:hover,
.export-action.active {
  color: #fff;
}

.forecast-panel {
  position: fixed;
  top: 108px;
  right: 0;
  bottom: 138px;
  z-index: 30;
  width: max(min(25vw, 430px), calc(20vw + 4px));
  overflow: auto;
  padding: 12px;
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
  margin-bottom: 16px;
}

.kicker {
  color: #67e8d0;
  font-size: 15px;
  letter-spacing: 1px;
}

.panel-title h2 {
  margin: 3px 0 0;
  font-size: 28px;
}

.panel-context {
  margin: 5px 0 0;
  color: #87a9ad;
  font-size: 16px;
}

.metric-switch button {
  min-height: 36px;
  border: 0;
  border-radius: 2px;
  color: #91afb3;
  background: transparent;
  cursor: pointer;
  font-size: 15px;
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
  margin-bottom: 18px;
  padding-bottom: 15px;
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
  margin: 12px 0 18px;
  border: 1px solid rgba(143, 207, 205, 0.14);
  background: rgba(143, 207, 205, 0.12);
}

.history-overview div,
.prediction-meta div,
.source-meta div {
  display: grid;
  gap: 7px;
  min-height: 76px;
  padding: 11px;
  background: rgba(8, 29, 39, 0.92);
}

.history-overview span,
.prediction-meta span,
.source-meta span {
  color: #94b8be;
  font-size: 15px;
}

.history-overview strong,
.prediction-meta strong,
.source-meta strong {
  overflow-wrap: anywhere;
  color: #eaf6f5;
  font-size: 16px;
  font-weight: 600;
}

.map-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin-top: 13px;
  color: #b5ccd0;
  font-size: 13px;
  line-height: 1.55;
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
  margin: 18px 0 10px;
  color: #67e8d0;
  font-size: 17px;
  font-weight: 600;
}

.section-heading small {
  color: #749399;
  font-size: 14px;
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
  min-height: 40px;
  padding: 0 10px;
  color: #dff8f4;
  font-size: 13px;
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
  font-size: 12px;
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
  font-size: 12px;
  cursor: pointer;
}

.export-option-group-title {
  display: block;
  margin: 8px 0 2px;
  color: #9fe8dc;
  font-size: 12px;
  font-weight: 600;
}

.export-legend-option {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 26px;
  color: #d5e9ee;
  font-size: 13px;
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
  font-size: 12px;
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
  gap: 8px;
  margin: 12px 0 18px;
  color: #b5ccd0;
  font-size: 17px;
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
  margin: 11px 0 5px;
  color: #94b8be;
  font-size: 16px;
}

.forecast-panel select {
  width: 100%;
  min-height: 44px;
  border: 1px solid #356a76;
  border-radius: 3px;
  padding: 8px 10px;
  color: #e4ffff;
  background: #0a2a38;
  font: 18px 'Microsoft YaHei', sans-serif;
}

.selection-info {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  margin: 15px 0;
  color: #70e2d3;
  font-size: 18px;
}

.selection-info span,
.prediction-readout span,
.environment-grid span,
.environment-grid small,
.comparison-snapshot span,
.analysis-metric-list span {
  color: #94b8be;
  font-size: 16px;
}

.horizon-group,
.analysis-horizons {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 4px;
  margin: 14px 0;
}

.horizon-group button,
.analysis-horizons button {
  min-height: 40px;
  padding: 5px 1px;
  font-size: 16px;
}

.prediction-readout {
  display: grid;
  gap: 12px;
  margin: 14px 0;
  padding: 15px;
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
  font-size: 17px;
  text-align: right;
}

.message {
  margin: 12px 0;
  color: #ffb1a2;
  font-size: 16px;
  line-height: 1.55;
}

.run-button {
  width: 100%;
  margin-top: 14px;
  border: 0;
  border-radius: 3px;
  padding: 16px;
  color: #06212a;
  background: #67e8d0;
  cursor: pointer;
  font-size: 18px;
  font-weight: 700;
}

.run-button:disabled {
  color: #789096;
  background: #2b4249;
  cursor: not-allowed;
}

.panel-note {
  display: block;
  margin-top: 15px;
  color: #749399;
  font-size: 15px;
  line-height: 1.7;
}

.forecast-subtabs {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px;
  margin: 0 0 18px;
  padding: 3px;
  border: 1px solid rgba(143, 207, 205, 0.2);
  background: rgba(8, 36, 48, 0.7);
}

.forecast-subtabs button {
  min-height: 42px;
  border: 0;
  border-radius: 2px;
  color: #9dbabc;
  background: transparent;
  cursor: pointer;
  font-size: 16px;
}

.forecast-subtabs button.active {
  color: #06212a;
  background: #67e8d0;
  font-weight: 700;
}

.impact-note {
  margin: 13px 0 0;
  padding-left: 10px;
  border-left: 2px solid #ef4444;
  color: #a9c0c4;
  font-size: 14px;
  line-height: 1.65;
}

.environment-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  color: #d3eeee;
  font-size: 14px;
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
  font-size: 12px;
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
  font-size: 13px;
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
  font-size: 13px;
}

.analysis-headline strong {
  display: block;
  margin-top: 5px;
  color: #eaf6f5;
  font-size: 15px;
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
  min-height: 44px;
  padding: 9px 0;
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

.history-data-panel {
  position: fixed;
  right: max(min(25vw, 430px), calc(20vw + 4px));
  bottom: 138px;
  left: calc(min(18vw, 400px) + 4px);
  z-index: 34;
  width: auto;
  min-width: 0;
  overflow: hidden;
  transform: none;
  border: 1px solid rgba(43, 185, 224, 0.62);
  color: #d8f4ff;
  background: linear-gradient(180deg, rgba(10, 42, 71, 0.25), rgba(5, 27, 48, 0.25));
  box-shadow: 0 6px 22px rgba(0, 0, 0, 0.35), 0 0 18px rgba(31, 151, 198, 0.12);
  font-family: 'Microsoft YaHei', sans-serif;
  pointer-events: auto;
}

.history-data-heading {
  display: flex;
  min-height: 30px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 5px 12px;
  border-bottom: 1px solid rgba(68, 174, 214, 0.25);
  background: rgba(12, 44, 74, 0.92);
}

.history-data-heading > div {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
}

.history-data-title {
  color: #a8e8ff;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 1.5px;
  white-space: nowrap;
}

.history-data-heading small {
  overflow: hidden;
  color: #6c9fbb;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-intensity-legend {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 10px;
  margin: 0;
  padding: 0;
  color: #9db8c8;
  font-size: 12px;
  white-space: nowrap;
}

.history-intensity-legend li {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.history-intensity-legend i {
  display: inline-block;
  width: 9px;
  height: 9px;
  border-radius: 1px;
}

.history-data-live {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 5px;
  color: #3ddc84;
  font-size: 13px;
  white-space: nowrap;
}

.history-data-live i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #3ddc84;
  box-shadow: 0 0 8px rgba(61, 220, 132, 0.8);
}

.history-data-heading > .history-data-actions {
  flex: 0 0 auto;
  flex-wrap: nowrap;
  align-items: center;
  gap: 8px;
}

.history-data-toggle {
  min-width: 48px;
  height: 24px;
  border: 1px solid rgba(103, 232, 208, 0.42);
  border-radius: 2px;
  padding: 0 8px;
  color: #9ed0ca;
  background: rgba(103, 232, 208, 0.06);
  cursor: pointer;
  font: 500 12px/1 'Microsoft YaHei', sans-serif;
  letter-spacing: 1px;
}

.history-data-toggle:hover,
.history-data-toggle:focus-visible {
  color: #06212a;
  background: #67e8d0;
  outline: none;
}

.history-data-panel.is-collapsed .history-intensity-legend,
.history-data-panel.is-collapsed .history-data-live {
  display: none;
}

.history-data-table-wrap {
  height: clamp(190px, 28vh, 320px);
  background: rgba(13, 43, 71, 0.25);
  overflow-x: hidden;
  overflow-y: auto;
  scrollbar-color: #2aa8cc rgba(13, 43, 71, 0.72);
  scrollbar-width: thin;
}

.history-data-table-wrap::-webkit-scrollbar {
  width: 7px;
}

.history-data-table-wrap::-webkit-scrollbar-track {
  background: rgba(13, 43, 71, 0.72);
}

.history-data-table-wrap::-webkit-scrollbar-thumb {
  border-radius: 4px;
  background: #2aa8cc;
}

.history-data-table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
  font-size: 14px;
}

.history-data-table th,
.history-data-table td {
  overflow: hidden;
  height: 34px;
  padding: 0 8px;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-data-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  height: 32px;
  color: #7fc3de;
  background: #123a5e;
  font-size: 12px;
  font-weight: 500;
}

.history-data-table td {
  border-top: 1px solid rgba(44, 112, 151, 0.22);
  color: #9cc8de;
  background: rgba(13, 43, 71, 0.25);
  transition: color 160ms ease, background 160ms ease;
}

.history-data-table tbody tr:nth-child(even) td {
  background: rgba(13, 43, 71, 0.25);
}

.history-data-table tbody tr:hover td,
.history-data-table tbody tr.current td {
  color: #ffffff;
  background: rgba(31, 169, 201, 0.48);
}

.history-data-table td.accent-speed {
  color: #f2d24b;
}

.history-data-table td.accent-pressure {
  color: #ff9a5a;
}

.legacy-timeline {
  position: relative;
  grid-row: 3;
  z-index: 35;
  display: grid;
  grid-template-columns: calc(min(18vw, 400px) - 34px) calc(100vw - min(18vw, 400px) - 4px - max(min(25vw, 430px), calc(20vw + 4px))) minmax(160px, 1fr) 54px;
  gap: 16px;
  align-items: center;
  min-height: 0;
  padding: 5px 22px;
  border-top: 1px solid rgba(103, 232, 208, 0.32);
  color: #e4ffff;
  background: rgba(3, 24, 38, 0.94);
  box-shadow: 0 -10px 28px rgba(0, 0, 0, 0.24);
  font-family: 'Microsoft YaHei', sans-serif;
  box-sizing: border-box;
  resize: none;
}

.viewport-readout {
  grid-row: 4;
  display: flex;
  align-items: center;
  gap: 24px;
  min-width: 0;
  padding: 0 22px;
  border-top: 1px solid rgba(33, 82, 117, 0.5);
  color: rgba(178, 221, 235, 0.86);
  background: #031724;
  font: 13px 'Microsoft YaHei', sans-serif;
  white-space: nowrap;
  box-sizing: border-box;
}

.legacy-timeline-control,
.legacy-replay-readout {
  display: flex;
  align-items: center;
}

.legacy-timeline-control {
  gap: 8px;
}

.legacy-timeline-control strong,
.legacy-replay-readout strong {
  display: block;
  color: #eaf6f5;
  font-size: 18px;
  font-weight: 600;
}

.legacy-timeline-control small,
.legacy-replay-readout span {
  color: #7f9da7;
  font-size: 15.5px;
}

.legacy-timeline-control small {
  display: block;
  margin-top: 3px;
}

.legacy-play-button {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  border: 1px solid rgba(103, 232, 208, 0.56);
  border-radius: 2px;
  color: #06212a;
  background: #67e8d0;
  cursor: pointer;
  font-size: 21.5px;
}

.legacy-play-button:disabled,
.legacy-reset-button:disabled {
  color: #789096;
  background: rgba(43, 66, 73, 0.8);
  cursor: not-allowed;
}

.legacy-timeline-track {
  position: relative;
  min-width: 0;
}

.legacy-timeline-track::before {
  content: '';
  position: absolute;
  top: 50%;
  right: 0;
  left: 0;
  height: 10px;
  border-radius: 3px;
  --fill: calc(20px + var(--progress) * (100% - 40px));
  background: linear-gradient(90deg, #67e8d0 0 var(--fill), rgba(143, 207, 205, 0.2) var(--fill) 100%);
  transform: translateY(-50%);
  pointer-events: none;
}

.legacy-timeline-track input {
  position: relative;
  display: block;
  box-sizing: border-box;
  width: 100%;
  height: 10px;
  margin: 0;
  padding: 0 11px;
  appearance: none;
  outline: none;
  background: transparent;
  cursor: pointer;
}

.legacy-timeline-track input::-webkit-slider-thumb {
  width: 18px;
  height: 18px;
  appearance: none;
  border: 2px solid #06212a;
  border-radius: 50%;
  background: #f7c873;
  box-shadow: 0 0 0 2px #67e8d0;
}

.legacy-timeline-track input::-moz-range-thumb {
  width: 14px;
  height: 14px;
  border: 2px solid #06212a;
  border-radius: 50%;
  background: #f7c873;
  box-shadow: 0 0 0 2px #67e8d0;
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
  min-height: 34px;
  border: 1px solid rgba(143, 207, 205, 0.3);
  border-radius: 2px;
  color: #67e8d0;
  background: transparent;
  cursor: pointer;
  font-size: 15.5px;
}

.legacy-reset-button:hover {
  border-color: #67e8d0;
  background: rgba(103, 232, 208, 0.1);
}

.legacy-timeline.is-empty .legacy-timeline-track::before {
  --fill: 0px;
}

.legacy-timeline.is-empty .legacy-timeline-track input {
  cursor: not-allowed;
}

@media (max-width: 760px) {
  .screen {
    grid-template-rows: 102px minmax(0, 1fr) 50px 24px;
  }

  .screen-header {
    height: 102px;
    min-height: 0;
    padding: 8px;
    flex-wrap: wrap;
    align-content: center;
    gap: 7px;
  }

  .screen-header-status {
    margin-left: auto;
    font-size: 11px;
  }

  .status-connector,
  .runtime-status {
    display: none;
  }

  .export-action {
    flex: 0 0 54px;
    font-size: 0;
  }

  .screen-brand {
    min-width: 0;
    flex: 1 1 auto;
  }

  .screen-brand-mark {
    flex-basis: 36px;
    width: 36px;
    height: 36px;
  }

  .screen-brand-mark svg {
    width: 28px;
    height: 28px;
  }

  .screen-brand h1 {
    font-size: 16px;
    letter-spacing: 1px;
  }

  .screen-brand-subtitle {
    margin-top: 3px;
    font-size: 9px;
  }

  .top-actions {
    order: 3;
    width: 100%;
    height: 48px;
  }

  .nav-item {
    min-width: 0;
    height: 46px;
    gap: 2px;
    padding: 3px 2px;
    font-size: 9px;
  }

  .nav-item svg {
    flex-basis: 19px;
    width: 19px;
    height: 19px;
  }

  .nav-item small {
    display: none;
  }

  .forecast-panel {
    top: 108px;
    right: 8px;
    bottom: 184px;
    width: calc(100vw - 16px);
  }

  .history-data-panel {
    right: 8px;
    bottom: 82px;
    left: 8px;
    width: auto;
    min-width: 0;
    transform: none;
  }

  .history-data-heading {
    min-height: 28px;
    padding: 4px 8px;
  }

  .history-data-heading > div {
    gap: 6px;
  }

  .history-data-title {
    font-size: 10px;
    letter-spacing: 1px;
  }

  .history-data-heading small {
    font-size: 8px;
  }

  .history-data-table {
    font-size: 9px;
  }

  .history-data-table-wrap {
    height: 88px;
  }

  .history-data-table th,
  .history-data-table td {
    height: 22px;
    padding: 0 3px;
  }

  .history-data-table th {
    font-size: 8px;
  }

  .history-data-table th:nth-child(2),
  .history-data-table td:nth-child(2),
  .history-data-table th:nth-child(3),
  .history-data-table td:nth-child(3) {
    display: none;
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
    grid-template-columns: 106px minmax(0, 1fr) 42px;
    gap: 8px;
    padding: 5px 8px;
  }

  .legacy-replay-readout {
    display: none;
  }

  .viewport-readout {
    gap: 10px;
    padding: 0 8px;
    font-size: 9px;
  }

}

@media (max-width: 1100px) and (min-width: 761px) {
  .screen-brand h1 {
    font-size: 18px;
    letter-spacing: 1px;
  }

  .screen-brand-subtitle {
    font-size: 9px;
  }

  .screen-header {
    gap: 7px;
    padding: 0 12px;
  }

  .screen-header-status {
    font-size: 11px;
  }

  .top-actions {
    flex: 1 1 auto;
  }

  .nav-item {
    min-width: 68px;
    padding: 5px 4px;
    font-size: 10px;
  }

  .nav-item svg {
    flex-basis: 20px;
    width: 20px;
    height: 20px;
  }
}

@media (min-width: 1200px) {
  .top-actions {
    flex: 0 0 auto;
    width: clamp(600px, 32vw, 820px);
  }
}

@media (max-height: 560px) and (min-width: 761px) {
  .screen {
  grid-template-rows: 56px minmax(0, 1fr) 46px 22px;
  }

  .screen-header {
    height: 56px;
  }

  .screen-brand-mark {
    flex-basis: 36px;
    width: 36px;
    height: 36px;
  }

  .top-actions,
  .nav-item {
    height: 46px;
  }

  .legacy-timeline {
    gap: 10px;
    padding: 3px 16px;
  }

  .forecast-panel {
    top: 56px;
    bottom: 68px;
  }

  .history-data-panel {
    bottom: 84px;
  }
}

.analysis-panel {
  border-color: rgba(43, 185, 224, 0.62);
  color: #d8f4ff;
  background: linear-gradient(180deg, rgba(10, 42, 71, 0.98), rgba(5, 27, 48, 0.98));
  box-shadow: 0 6px 22px rgba(0, 0, 0, 0.35), 0 0 18px rgba(31, 151, 198, 0.12);
}

.analysis-panel .panel-title {
  margin: -12px -12px 12px;
  padding: 9px 12px;
  border-bottom: 1px solid rgba(68, 174, 214, 0.25);
  background: rgba(12, 44, 74, 0.92);
}

.analysis-panel .kicker,
.analysis-panel .section-heading,
.analysis-panel .selection-info {
  color: #7fc3de;
}

.analysis-panel .panel-title h2 {
  font-size: 20px;
  line-height: 1.3;
}

.analysis-panel .panel-context,
.analysis-panel .section-heading small,
.analysis-panel .panel-note {
  color: #6c9fbb;
}

.analysis-panel .selection-block {
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom-color: rgba(68, 174, 214, 0.25);
}

.analysis-panel .field-label,
.analysis-panel .history-overview span,
.analysis-panel .prediction-meta span,
.analysis-panel .source-meta span,
.analysis-panel .selection-info span,
.analysis-panel .prediction-readout span,
.analysis-panel .environment-grid span,
.analysis-panel .environment-grid small,
.analysis-panel .comparison-snapshot span,
.analysis-panel .analysis-metric-list span {
  color: #82abc2;
}

.analysis-panel .forecast-panel select,
.analysis-panel select {
  border-color: rgba(61, 155, 195, 0.55);
  color: #e1f5ff;
  background: rgba(9, 39, 65, 0.95);
}

.analysis-panel .history-overview,
.analysis-panel .prediction-meta,
.analysis-panel .source-meta,
.analysis-panel .environment-grid,
.analysis-panel .comparison-snapshot,
.analysis-panel .analysis-metric-list {
  border-color: rgba(44, 112, 151, 0.35);
  background: rgba(44, 112, 151, 0.24);
}

.analysis-panel .history-overview div,
.analysis-panel .prediction-meta div,
.analysis-panel .source-meta div,
.analysis-panel .environment-grid div {
  background: rgba(8, 32, 55, 0.94);
}

.analysis-panel .history-overview strong,
.analysis-panel .prediction-meta strong,
.analysis-panel .source-meta strong,
.analysis-panel .prediction-readout strong,
.analysis-panel .environment-grid strong,
.analysis-panel .comparison-snapshot strong,
.analysis-panel .analysis-metric-list strong {
  color: #e1f5ff;
}

.analysis-panel .forecast-subtabs,
.analysis-panel .metric-switch {
  border-color: rgba(43, 185, 224, 0.32);
  background: rgba(13, 43, 71, 0.72);
}

.analysis-panel .forecast-subtabs button,
.analysis-panel .metric-switch button {
  color: #9cc8de;
}

.analysis-panel .forecast-subtabs button.active,
.analysis-panel .metric-switch button.active {
  color: #06243a;
  background: #2bb9e0;
}

.analysis-panel .horizon-group button,
.analysis-panel .analysis-horizons button {
  border-color: rgba(68, 174, 214, 0.32);
  color: #9cc8de;
  background: rgba(13, 43, 71, 0.72);
}

.analysis-panel .horizon-group button.active,
.analysis-panel .analysis-horizons button.active {
  border-color: #2bb9e0;
  color: #06243a;
  background: #2bb9e0;
}

.analysis-panel .prediction-readout {
  border-left-color: #f7c873;
  background: rgba(13, 53, 82, 0.72);
}

.analysis-panel .environment-grid strong {
  color: #f7c873;
}

.analysis-panel .risk-radius strong {
  color: #f7c873;
}

.analysis-panel .coverage-bar span {
  background-color: #f7c873;
}

.analysis-panel .wind-vector-readout {
  border-left-color: #f7c873;
  background: rgba(13, 53, 82, 0.72);
}

.analysis-panel .wind-arrow {
  color: #f7c873;
}

.analysis-panel .risk-radius {
  border-left-color: #f7c873;
  background: rgba(60, 48, 27, 0.55);
}

.analysis-panel .risk-radius span {
  color: #c7b081;
}

.analysis-panel .analysis-headline {
  border-bottom-color: rgba(68, 174, 214, 0.25);
  color: #7fc3de;
}

.analysis-panel .comparison-snapshot div,
.analysis-panel .analysis-metric-list div {
  border-bottom-color: rgba(44, 112, 151, 0.24);
}

.analysis-panel .comparison-snapshot strong,
.analysis-panel .analysis-metric-list strong {
  color: #f7c873;
}

.analysis-panel .coverage-bar {
  background: rgba(44, 112, 151, 0.32);
}

.analysis-panel .audit-status,
.analysis-panel .risk-boundary {
  border-left-color: #2bb9e0;
  color: #d8f4ff;
  background: rgba(13, 53, 82, 0.72);
}

.analysis-panel .risk-boundary strong {
  color: #f7c873;
}

.analysis-panel .audit-status small,
.analysis-panel .risk-boundary span {
  color: #8db5ca;
}

.analysis-panel .close-button {
  border-color: rgba(68, 174, 214, 0.5);
  color: #a8e8ff;
  background: rgba(13, 58, 91, 0.7);
}

.analysis-panel .run-button {
  border: 1px solid rgba(89, 202, 235, 0.65);
  color: #06243a;
  background: #2bb9e0;
}

.analysis-panel .run-button:hover:not(:disabled) {
  background: #69d2f0;
}

.analysis-panel .run-button:disabled {
  border-color: rgba(68, 112, 136, 0.3);
  color: #7896a7;
  background: rgba(25, 54, 73, 0.9);
}

.analysis-panel .panel-note {
  font-size: 12px;
  line-height: 1.55;
}

.analysis-panel-export {
  border-color: rgba(92, 211, 210, 0.78);
  color: #e4ffff;
  background: rgba(3, 24, 38, 0.96);
}

.analysis-panel-export .panel-title {
  margin: 0 0 16px;
  padding: 0;
  border: 0;
  background: transparent;
}
</style>
