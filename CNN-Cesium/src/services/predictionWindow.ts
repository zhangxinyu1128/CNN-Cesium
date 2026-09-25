import type { PredictionHistoryPoint, TyphoonPoint } from '@/types/typhoon'

const STEP_MS = 6 * 60 * 60 * 1000
const MAX_GAP_MS = 9 * 60 * 60 * 1000
const FEATURES = ['lng', 'lat', 'speed', 'power'] as const
type Feature = (typeof FEATURES)[number]

function timestamp(value: string) {
  const normalized = /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value}Z`
  return Date.parse(normalized)
}

function sampleAt(
  points: Array<{ point: TyphoonPoint; time: number }>,
  targetTime: number,
  feature: Feature
): number | null {
  let left: { point: TyphoonPoint; time: number; value: number } | null = null

  for (const sample of points) {
    const value = sample.point[feature]
    if (sample.time === targetTime) {
      if (typeof value !== 'number' || !Number.isFinite(value)) return null
      return feature === 'lng' ? ((value % 360) + 360) % 360 : value
    }
    if (typeof value !== 'number' || !Number.isFinite(value)) continue
    if (sample.time < targetTime) {
      left = { ...sample, value }
      continue
    }
    if (sample.time > targetTime && left) {
      const duration = sample.time - left.time
      if (duration > MAX_GAP_MS) return null
      const rightValue = value
      let difference = rightValue - left.value
      if (feature === 'lng') difference = ((difference + 180) % 360 + 360) % 360 - 180
      const interpolated = left.value + (targetTime - left.time) / duration * difference
      return feature === 'lng' ? ((interpolated % 360) + 360) % 360 : interpolated
    }
    return null
  }
  return null
}

export type PredictionWindowResult =
  | { ok: true; history: PredictionHistoryPoint[]; originTime: string }
  | { ok: false; reason: string }

export function buildPredictionWindow(
  points: TyphoonPoint[],
  currentIndex: number
): PredictionWindowResult {
  const available = points
    .slice(0, currentIndex + 1)
    .map((point) => ({ point, time: timestamp(point.time) }))
    .filter((sample) => Number.isFinite(sample.time))
    .sort((a, b) => a.time - b.time)

  if (available.length < 2) {
    return { ok: false, reason: '当前时间点之前没有足够的有效观测。' }
  }

  const firstTime = available[0].time
  const currentTime = available[available.length - 1].time
  const gridSteps = Math.floor((currentTime - firstTime) / STEP_MS)
  if (gridSteps < 4) {
    return { ok: false, reason: '至少需要连续 30 小时数据来构造 5 个六小时观测点。' }
  }

  const originTime = firstTime + gridSteps * STEP_MS
  if (currentTime - originTime > MAX_GAP_MS) {
    return { ok: false, reason: '当前时刻与最后一个六小时观测点相隔超过 9 小时。' }
  }
  const history: PredictionHistoryPoint[] = []
  for (let offset = -4; offset <= 0; offset += 1) {
    const targetTime = originTime + offset * STEP_MS
    const values = {} as Record<Feature, number | null>
    for (const feature of FEATURES) values[feature] = sampleAt(available, targetTime, feature)
    if (values.lng === null || values.lat === null) {
      return { ok: false, reason: '最近 30 小时轨迹存在超过 9 小时的数据缺口。' }
    }
    if (offset > -4 && (values.speed === null || values.power === null)) {
      return { ok: false, reason: '最近四个六小时观测缺少风速或风力，无法执行模型推理。' }
    }
    const item: PredictionHistoryPoint = {
      time: new Date(targetTime).toISOString(),
      lng: values.lng,
      lat: values.lat,
      speed: values.speed,
      power: values.power
    }
    history.push(item)
  }

  return { ok: true, history, originTime: history[history.length - 1].time }
}
