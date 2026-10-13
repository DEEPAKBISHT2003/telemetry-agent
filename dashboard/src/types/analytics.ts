export interface TokenUsageBreakdown {
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  thinking_tokens: number | null;
  total_tokens: number;
}

export interface AnalyticsOverviewResponse {
  organization_id: string | null;
  start_date: string;
  end_date: string;
  total_developers: number;
  active_developers: number;
  enrolled_devices: number;
  active_devices: number;
  total_events: number;
  total_ai_runs: number;
  total_tool_calls: number;
  total_sessions: number;
  tokens: TokenUsageBreakdown;
  total_cost_usd: string | number | null;
  first_event_at: string | null;
  last_event_at: string | null;
}

export interface TimeseriesBucket {
  timestamp: string;
  events_count: number;
  ai_runs_count: number;
  tool_calls_count: number;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: string | number | null;
}

export interface UsageTimeseriesResponse {
  interval: string;
  start_date: string;
  end_date: string;
  buckets: TimeseriesBucket[];
}

export interface DeveloperSummary {
  developer_id: string;
  email: string;
  full_name: string;
  role: string;
  status: string;
  teams: string[];
  event_count: number;
  ai_run_count: number;
  total_tokens: number;
  input_tokens: number;
  cached_tokens: number;
  output_tokens: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  is_active: boolean;
}

export interface DeveloperListResponse {
  items: DeveloperSummary[];
  total_count: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface ModelUsageSummary {
  model_name: string;
  run_count: number;
  total_tokens: number;
  last_used_at: string | null;
}

export interface SessionBrief {
  session_id: string;
  repository_name: string | null;
  branch: string | null;
  event_count: number;
  ai_run_count: number;
  total_tokens: number;
  started_at: string | null;
  last_activity_at: string | null;
}

export interface DeveloperDetailResponse {
  developer: DeveloperSummary;
  models: ModelUsageSummary[];
  recent_sessions: SessionBrief[];
  enrolled_devices_count: number;
}

export interface ModelAnalyticsSummary {
  model_name: string;
  ai_run_count: number;
  total_events: number;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  thinking_tokens: number | null;
  total_tokens: number;
  cost_usd: string | number | null;
  last_used_at: string | null;
}

export interface ModelAnalyticsResponse {
  items: ModelAnalyticsSummary[];
  total_models: number;
}

export interface SessionAnalyticsSummary {
  session_id: string;
  developer_id: string;
  developer_name: string;
  repository_name: string | null;
  branch: string | null;
  event_count: number;
  ai_run_count: number;
  input_tokens: number;
  cached_tokens: number;
  output_tokens: number;
  total_tokens: number;
  started_at: string | null;
  last_activity_at: string | null;
  primary_model: string | null;
}

export interface SessionListResponse {
  items: SessionAnalyticsSummary[];
  total_count: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface DeviceAnalyticsSummary {
  device_id: string;
  developer_id: string;
  developer_name: string;
  hostname: string;
  platform: string;
  agent_version: string;
  status: string;
  total_events: number;
  last_seen_at: string | null;
}

export interface DeviceListResponse {
  items: DeviceAnalyticsSummary[];
  total_count: number;
}

export type NavigationTab = "overview" | "developers" | "models" | "sessions" | "devices";

export type DateRangePreset = "7d" | "30d" | "90d" | "all";
