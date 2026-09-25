import type {
  HealthResponse,
  PredictionHistoryPoint,
  PredictionResponse,
  TyphoonDetail,
  TyphoonIndexItem
} from '@/types/typhoon'

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
  return request<number[]>('/api/years.json')
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
