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
  radius7?: number[] | null
  radius10?: number[] | null
  remark?: string | null
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

export type ExportLegendId = 'history' | 'prediction' | 'error' | 'impact'
export type ExportInfoId = 'time' | 'position' | 'intensity' | 'era5'

export interface ExportSettings {
  visible_legend_ids: ExportLegendId[]
  visible_info_ids: ExportInfoId[]
}

export interface PredictionHistoryPoint {
  time: string
  lng: number
  lat: number
  speed?: number | null
  power?: number | null
}

export interface PredictionRegion {
  geometry: string
  coverage: number
  semi_major_axis_km: number
  semi_minor_axis_km: number
  bearing_deg?: number
  area_km2?: number
  interpretation?: string | null
}

export interface PredictionSpeedInterval {
  lower: number
  upper: number
  unit: string
  interpretation?: string | null
}

export interface PredictionPoint {
  lead_hours: number
  lng: number
  lat: number
  speed_ms: number | null
  p05: { lng: number; lat: number } | null
  p95: { lng: number; lat: number } | null
  location_radius_90_km?: number | null
  uncertainty_region?: PredictionRegion | null
  speed_interval_source?: PredictionSpeedInterval | null
}

export interface PredictionResponse {
  model_version: string
  generated_at: string
  typhoon_id: string | null
  input_window: number
  horizons_hours: number[]
  predictions: PredictionPoint[]
  uncertainty?: {
    status: string
    target_coverage?: number | null
    method?: string | null
    calibration_storms?: number | null
    interpretation?: string | null
    region_geometry?: string | null
    region_note?: string | null
    joint_region_geometry?: string | null
    joint_region_note?: string | null
  } | null
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

export interface Era5WindLevel {
  u: number
  v: number
  speed_ms: number
  direction_deg: number
}

export interface Era5Point {
  time: string
  lng: number
  lat: number
  era5_time_utc: string
  era5_age_hours: number
  levels: Record<string, Era5WindLevel>
  shear_500_850_ms: number | null
}

export interface Era5Response {
  typhoon_id: string
  status: string
  source: string
  levels_hpa: number[]
  matched_points: number
  points: Era5Point[]
}

export interface ExperimentMetricPoint {
  lead_hours: number
  path_mae_km: number
  path_rmse_km: number
  path_median_km?: number
  path_mae_km_std?: number
  path_rmse_km_std?: number
  path_median_km_std?: number
  wind_mae_ms?: number
  wind_mae_ms_std?: number
  wind_rmse_ms?: number
  wind_rmse_ms_std?: number
  run_count?: number
}

export interface ExperimentModelSummary {
  key: string
  name: string
  kind: string
  feature_set?: string
  seed_count?: number
  seeds?: number[]
  sample_count?: number
  by_horizon: ExperimentMetricPoint[]
}

export interface ExperimentSummary {
  status: string
  source?: string
  source_files?: string[]
  device?: string
  test_samples?: number
  test_storms?: number
  available_horizons?: number[]
  levels_hpa?: number[]
  models: ExperimentModelSummary[]
  uncertainty: {
    status: string
    model_key?: string
    model_version?: string
    method?: string
    target_coverage?: number
    calibration_storms?: number
    evaluation_storms?: number
    evaluation_windows?: number
    source?: string
    interpretation?: string
    by_horizon?: Array<{
      lead_hours: number
      location_coverage_90: number
      location_radius_90_km: number
      joint_coverage_90: number
      location_storm_coverage_90?: number
      median_error_km?: number
      ellipse_90?: {
        geometry: string
        location_coverage_90: number
        location_storm_coverage_90: number
        semi_major_axis_km: number
        semi_minor_axis_km: number
        bearing_deg: number
        area_km2: number
        mean_error_km: number
      }
      energy_score_km?: number
    }>
  }
  official_forecast?: {
    status: string
    reason?: string | null
    comparable_positions?: number
    test_forecast_origins?: number
    test_forecast_positions?: number
    test_windows_scored?: number
    storms_scored?: string[]
    supported_model_leads_hours?: number[]
    by_horizon?: Array<{
      lead_hours: number
      sample_count: number
      storm_count: number
      mae_km: number
      rmse_km: number
      median_km?: number
      storm_mean_mae_km?: number
      p90_km?: number
    }>
    limitations?: string[]
    source?: string
  }
  era5?: {
    source?: string
    levels_hpa?: number[]
    variables?: string[]
    track_points?: number
    paired_points?: number
    paired_point_fraction?: number
    years?: number[]
    features?: string[]
    sample_type?: string
  }
  split?: {
    rule?: string
    train_years?: number[]
    validation_years?: number[]
    test_years?: number[]
    test_storms?: number
  }
  dataset?: {
    track_years?: Array<number | null>
    valid_tracks?: number
    track_points?: number
    fingerprint_sha256?: string
  }
  physics_constraint?: {
    status: string
    reason?: string
  }
  limitations?: string[]
}
