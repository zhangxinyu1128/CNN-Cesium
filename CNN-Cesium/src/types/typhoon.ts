export interface TyphoonPoint {
  time: string
  lng: number
  lat: number
  strong?: string
  power?: number | null
  speed?: number | null
  pressure?: number | null
  move_dir?: number | null
  move_speed?: number | null
}

export interface TyphoonIndexItem {
  tfbh: string
  ident?: string
  name?: string
  ename?: string
  is_current?: number
  begin_time?: string
  end_time?: string
  land?: Array<{
    position?: string
    land_time?: string
    lng?: number
    lat?: number
  }>
}

export interface TyphoonDetail extends TyphoonIndexItem {
  points: TyphoonPoint[]
}

export interface PredictionHistoryPoint {
  time: string
  lng: number
  lat: number
  speed?: number | null
  power?: number | null
}

export interface PredictionPoint {
  lead_hours: number
  lng: number
  lat: number
  speed_ms: number | null
  p05: { lng: number; lat: number } | null
  p95: { lng: number; lat: number } | null
}

export interface PredictionResponse {
  model_version: string
  generated_at: string
  typhoon_id: string | null
  input_window: number
  horizons_hours: number[]
  predictions: PredictionPoint[]
}

export interface HealthResponse {
  status: string
  service: string
  data: {
    status: string
    root: string
    year_files: number
    typhoon_files: number
    empty_typhoon_files: number
  }
  model: {
    status: string
    path: string
    exists: boolean
    size_bytes: number
    model_version: string | null
    device: string | null
    checkpoint_sha256: string | null
    reason: string | null
  }
}
