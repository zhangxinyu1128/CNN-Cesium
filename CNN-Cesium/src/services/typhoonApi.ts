import type {
  HealthResponse,
  Era5Response,
  ExportInfoId,
  ExportLegendId,
  ExportSettings,
  ExperimentSummary,
  PredictionHistoryPoint,
  PredictionResponse,
  TyphoonDetail,
  TyphoonIndexItem
} from '@/types/typhoon'

export const EXPORT_LEGEND_OPTIONS = [
  { id: 'history', label: '历史路径', color: '#4da3ff', kind: 'line' },
  { id: 'prediction', label: 'CNN 预测路径', color: '#67e8d0', kind: 'dashed' },
  { id: 'error', label: '历史校准误差椭圆', color: '#f7c873', kind: 'box' },
  { id: 'impact', label: '模型估计影响范围', color: '#ef4444', kind: 'box' }
] as const

export const EXPORT_INFO_OPTIONS = [
  { id: 'time', label: '回放时间', color: '#ffffff', kind: 'text' },
  { id: 'position', label: '位置经纬度', color: '#ffffff', kind: 'text' },
  { id: 'intensity', label: '历史强度与风速', color: '#ffffff', kind: 'text' },
  { id: 'era5', label: 'ERA5 风场数据', color: '#f7c873', kind: 'text' }
] as const

export type { ExportInfoId, ExportLegendId } from '@/types/typhoon'

export const DEFAULT_EXPORT_LEGEND_IDS: ExportLegendId[] = EXPORT_LEGEND_OPTIONS.map(
  (item) => item.id
)
export const DEFAULT_EXPORT_INFO_IDS: ExportInfoId[] = EXPORT_INFO_OPTIONS.map((item) => item.id)

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const detail = body?.detail
    const message =
      typeof detail === 'string' ? detail : detail?.message || detail?.code || `HTTP ${response.status}`
    throw new Error(`请求失败: ${message}`)
  }
  return response.json() as Promise<T>
}

export function fetchYears() {
  return request<Array<number | { year?: number }>>('/api/years.json').then((items) =>
    items
      .map((item) => typeof item === 'number' ? item : item.year)
      .filter((year): year is number => typeof year === 'number')
  )
}

export function fetchTyphoonIndex(year: number, query = '') {
  const params = new URLSearchParams({
    year: String(year),
    limit: '100'
  })
  if (query) {
    params.set('q', query)
  }
  return request<TyphoonIndexItem[]>(`/api/typhoons?${params.toString()}`)
}

export function fetchTyphoon(id: string) {
  return request<TyphoonDetail>(`/api/typhoons/${encodeURIComponent(id)}`)
}

export function fetchHealth() {
  return request<HealthResponse>('/health')
}

export function fetchTyphoonEra5(typhoonId: string) {
  return request<Era5Response>(`/api/typhoons/${encodeURIComponent(typhoonId)}/era5`)
}

export function fetchExperimentSummary() {
  return request<ExperimentSummary>('/api/experiments/summary')
}

export function fetchExportSettings() {
  return request<ExportSettings>('/api/export-settings')
}

export function saveExportSettings(settings: ExportSettings) {
  return request<ExportSettings>('/api/export-settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings)
  })
}

export function predictTyphoon(typhoonId: string, history: PredictionHistoryPoint[]) {
  return request<PredictionResponse>('/api/predict', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      typhoon_id: typhoonId,
      history,
      horizons_hours: [6, 12, 18, 24, 30, 36]
    })
  })
}
