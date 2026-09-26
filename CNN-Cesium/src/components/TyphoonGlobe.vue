<script setup lang="ts">
import * as Cesium from 'cesium'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type {
  PredictionHistoryPoint,
  PredictionPoint,
  TyphoonPoint
} from '@/types/typhoon'
import 'cesium/Build/Cesium/Widgets/widgets.css'

const props = withDefaults(
  defineProps<{
    points: TyphoonPoint[]
    currentIndex: number
    showTrack?: boolean
    forecastStart?: PredictionHistoryPoint | null
    predictions?: PredictionPoint[]
  }>(),
  {
    showTrack: true,
    forecastStart: null,
    predictions: () => []
  }
)

const emit = defineEmits<{
  ready: []
}>()

const container = ref<HTMLDivElement | null>(null)
let viewer: Cesium.Viewer | null = null
let trackEntity: Cesium.Entity | null = null
let currentEntity: Cesium.Entity | null = null
let forecastEntity: Cesium.Entity | null = null
let pointEntities: Cesium.Entity[] = []
let forecastPointEntities: Cesium.Entity[] = []
let validTrackPoints: TyphoonPoint[] = []

const colors = [
  Cesium.Color.fromCssColorString('#6bd7c7'),
  Cesium.Color.fromCssColorString('#f7c873'),
  Cesium.Color.fromCssColorString('#ff8b75'),
  Cesium.Color.fromCssColorString('#ff5c72')
]

function powerColor(power?: number | null) {
  if (!power) return colors[0]
  if (power >= 14) return colors[3]
  if (power >= 12) return colors[2]
  if (power >= 10) return colors[1]
  return colors[0]
}

function clearTrack() {
  if (!viewer) return
  if (trackEntity) viewer.entities.remove(trackEntity)
  if (currentEntity) viewer.entities.remove(currentEntity)
  if (forecastEntity) viewer.entities.remove(forecastEntity)
  pointEntities.forEach((entity) => viewer?.entities.remove(entity))
  forecastPointEntities.forEach((entity) => viewer?.entities.remove(entity))
  trackEntity = null
  currentEntity = null
  forecastEntity = null
  pointEntities = []
  forecastPointEntities = []
  validTrackPoints = []
}

function renderForecast() {
  if (!viewer || !props.forecastStart || !props.predictions.length) return
  const forecastPoints = props.predictions.filter(
    (point) => Number.isFinite(point.lng) && Number.isFinite(point.lat)
  )
  if (!forecastPoints.length) return

  const start = props.forecastStart
  const positions = Cesium.Cartesian3.fromDegreesArray([
    start.lng,
    start.lat,
    ...forecastPoints.flatMap((point) => [point.lng, point.lat])
  ])
  forecastEntity = viewer.entities.add({
    name: 'CNN 预测路径',
    polyline: {
      positions,
      width: 3,
      material: new Cesium.PolylineDashMaterialProperty({
        color: Cesium.Color.fromCssColorString('#ff806f'),
        dashLength: 14
      })
    }
  })

  const originEntity = viewer.entities.add({
    name: `预测起点 ${start.time}`,
    position: Cesium.Cartesian3.fromDegrees(start.lng, start.lat),
    point: {
      pixelSize: 9,
      color: Cesium.Color.fromCssColorString('#f7c873'),
      outlineColor: Cesium.Color.fromCssColorString('#071720'),
      outlineWidth: 2,
      disableDepthTestDistance: Number.POSITIVE_INFINITY
    }
  })
  forecastPointEntities.push(originEntity)

  forecastPoints.forEach((point) => {
    const entity = viewer?.entities.add({
      name: `预测 +${point.lead_hours} 小时 · ${point.speed_ms?.toFixed(1) ?? '--'} m/s`,
      position: Cesium.Cartesian3.fromDegrees(point.lng, point.lat),
      point: {
        pixelSize: 7,
        color: Cesium.Color.fromCssColorString('#ff806f'),
        outlineColor: Cesium.Color.fromCssColorString('#071720'),
        outlineWidth: 1,
        disableDepthTestDistance: Number.POSITIVE_INFINITY
      }
    })
    if (entity) forecastPointEntities.push(entity)
  })
}

function updateCurrentMarker() {
  if (!viewer || !validTrackPoints.length || !currentEntity) return

  const index = Math.min(Math.max(props.currentIndex, 0), validTrackPoints.length - 1)
  const currentPoint = validTrackPoints[index]
  currentEntity.position = Cesium.Cartesian3.fromDegrees(currentPoint.lng, currentPoint.lat)
  currentEntity.label!.text = currentPoint.strong || '当前台风'

  pointEntities.forEach((entity, pointIndex) => {
    if (entity.point) entity.point.pixelSize = pointIndex === index ? 9 : 5
  })
}

function focusTrack() {
  if (!viewer || !trackEntity) return

  const cameraTarget = forecastEntity
    ? [trackEntity, forecastEntity, ...forecastPointEntities]
    : [trackEntity]
  void viewer.flyTo(cameraTarget, {
    duration: 1.1,
    // Keep the camera high enough to show the whole route without following each point.
    offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-68), 0)
  })
}

function renderTrack() {
  if (!viewer) return
  clearTrack()
  if (!props.points.length) return

  const validPoints = props.points.filter(
    (point) => Number.isFinite(point.lng) && Number.isFinite(point.lat)
  )
  if (!validPoints.length) return
  validTrackPoints = validPoints

  const positions = validPoints.flatMap((point) => [point.lng, point.lat])
  const linePositions = Cesium.Cartesian3.fromDegreesArray(positions)
  trackEntity = viewer.entities.add({
    name: '历史路径',
    polyline: {
      positions: linePositions,
      width: 4,
      material: new Cesium.PolylineGlowMaterialProperty({
        glowPower: 0.18,
        color: Cesium.Color.fromCssColorString('#67e8d0')
      }),
      show: props.showTrack
    }
  })

  validPoints.forEach((point, index) => {
    const entity = viewer?.entities.add({
      name: `${point.time} ${point.strong ?? ''}`,
      position: Cesium.Cartesian3.fromDegrees(point.lng, point.lat),
      point: {
        pixelSize: index === props.currentIndex ? 9 : 5,
        color: powerColor(point.power),
        outlineColor: Cesium.Color.fromCssColorString('#081621'),
        outlineWidth: 1,
        disableDepthTestDistance: Number.POSITIVE_INFINITY
      }
    })
    if (entity) pointEntities.push(entity)
  })

  const currentPoint = validPoints[Math.min(Math.max(props.currentIndex, 0), validPoints.length - 1)]
  currentEntity = viewer.entities.add({
    name: '当前时刻',
    position: Cesium.Cartesian3.fromDegrees(currentPoint.lng, currentPoint.lat),
    point: {
      pixelSize: 16,
      color: Cesium.Color.TRANSPARENT,
      outlineColor: Cesium.Color.fromCssColorString('#f7c873'),
      outlineWidth: 3,
      disableDepthTestDistance: Number.POSITIVE_INFINITY
    },
    label: {
      text: currentPoint.strong || '当前台风',
      font: '12px sans-serif',
      fillColor: Cesium.Color.WHITE,
      showBackground: true,
      backgroundColor: Cesium.Color.fromCssColorString('#102b38cc'),
      pixelOffset: new Cesium.Cartesian2(12, -12),
      disableDepthTestDistance: Number.POSITIVE_INFINITY
    }
  })

  renderForecast()

  focusTrack()
}

function resetView() {
  if (!viewer) return
  viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(135, 18, 22000000),
    orientation: {
      heading: 0,
      pitch: Cesium.Math.toRadians(-90),
      roll: 0
    },
    duration: 0.8
  })
}

defineExpose({ resetView })

onMounted(() => {
  if (!container.value) return
  const token = import.meta.env.VITE_CESIUM_TOKEN
  if (token) Cesium.Ion.defaultAccessToken = token

  viewer = new Cesium.Viewer(container.value, {
    baseLayer: false,
    terrainProvider: new Cesium.EllipsoidTerrainProvider(),
    animation: false,
    timeline: false,
    baseLayerPicker: false,
    geocoder: false,
    homeButton: false,
    sceneModePicker: false,
    navigationHelpButton: false,
    fullscreenButton: false,
    infoBox: false,
    selectionIndicator: false,
    scene3DOnly: true
  })
  viewer.scene.backgroundColor = Cesium.Color.fromCssColorString('#06121c')
  viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString('#0b2431')
  viewer.scene.globe.showGroundAtmosphere = true
  viewer.scene.globe.enableLighting = true
  viewer.scene.postProcessStages.fxaa.enabled = true
  void Cesium.TileMapServiceImageryProvider.fromUrl(
    Cesium.buildModuleUrl('Assets/Textures/NaturalEarthII'),
    { maximumLevel: 2 }
  )
    .then((provider) => {
      if (!viewer || viewer.isDestroyed()) return
      viewer.imageryLayers.addImageryProvider(provider)
    })
    .catch((error: unknown) => {
      // Keep local imagery failures visible in the browser console for diagnostics.
      // eslint-disable-next-line no-console
      console.error('Local Cesium Natural Earth imagery failed to load.', error)
    })
  resetView()
  emit('ready')
  renderTrack()
})

watch(
  () => [
    props.points,
    props.showTrack,
    props.forecastStart,
    props.predictions
  ],
  () => renderTrack(),
  { deep: true }
)

watch(
  () => props.currentIndex,
  () => updateCurrentMarker()
)

onBeforeUnmount(() => {
  viewer?.destroy()
  viewer = null
})
</script>

<template>
  <div ref="container" class="globe">
    <div v-if="!points.length" class="globe-empty">
      <span class="globe-empty-mark">◎</span>
      <strong>选择一个台风查看路径</strong>
      <small>历史轨迹会显示在三维地球上</small>
    </div>
  </div>
</template>
