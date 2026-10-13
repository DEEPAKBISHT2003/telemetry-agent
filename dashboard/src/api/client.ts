/**
 * HTTP API client for AI Telemetry Backend endpoints.
 * Integrates directly with /api/v1/analytics/* routes.
 */

import type {
  AnalyticsOverviewResponse,
  DeveloperDetailResponse,
  DeveloperListResponse,
  DeviceListResponse,
  ModelAnalyticsResponse,
  SessionListResponse,
  UsageTimeseriesResponse,
} from "../types/analytics";

const AUTH_KEY_STORAGE = "antigravity_telemetry_auth";
const BASE_URL = "/api/v1/analytics";

export function getStoredAuth(): string {
  return localStorage.getItem(AUTH_KEY_STORAGE) || "";
}

export function setStoredAuth(token: string): void {
  localStorage.setItem(AUTH_KEY_STORAGE, token.trim());
}

export function clearStoredAuth(): void {
  localStorage.removeItem(AUTH_KEY_STORAGE);
}

function buildHeaders(): HeadersInit {
  const auth = getStoredAuth();
  const headers: Record<string, string> = {
    Accept: "application/json",
  };

  if (auth) {
    if (auth.startsWith("dvt_") || auth.startsWith("Bearer ")) {
      headers["Authorization"] = auth.startsWith("Bearer ") ? auth : `Bearer ${auth}`;
    } else {
      headers["X-Admin-Key"] = auth;
    }
  }

  return headers;
}

async function request<T>(endpoint: string, params: Record<string, string | number | undefined> = {}): Promise<T> {
  const url = new URL(`${window.location.origin}${BASE_URL}${endpoint}`);
  Object.entries(params).forEach(([key, val]) => {
    if (val !== undefined && val !== null && val !== "") {
      url.searchParams.append(key, String(val));
    }
  });

  const res = await fetch(url.toString(), {
    method: "GET",
    headers: buildHeaders(),
  });

  if (!res.ok) {
    let errorMessage = `API Error: ${res.status} ${res.statusText}`;
    try {
      const errorJson = await res.json();
      if (errorJson.detail) {
        errorMessage = typeof errorJson.detail === "string" ? errorJson.detail : JSON.stringify(errorJson.detail);
      }
    } catch {
      // ignore json parse error
    }
    const err = new Error(errorMessage) as Error & { status: number };
    err.status = res.status;
    throw err;
  }

  return res.json() as Promise<T>;
}

export const api = {
  getOverview: (params?: { start_date?: string; end_date?: string; team_id?: string }) =>
    request<AnalyticsOverviewResponse>("/overview", params),

  getTimeseries: (params?: { start_date?: string; end_date?: string; developer_id?: string; model?: string }) =>
    request<UsageTimeseriesResponse>("/usage-timeseries", params),

  getDevelopers: (params?: {
    start_date?: string;
    end_date?: string;
    search?: string;
    team_id?: string;
    page?: number;
    page_size?: number;
  }) => request<DeveloperListResponse>("/developers", params),

  getDeveloperDetail: (developerId: string, params?: { start_date?: string; end_date?: string }) =>
    request<DeveloperDetailResponse>(`/developers/${developerId}`, params),

  getModels: (params?: { start_date?: string; end_date?: string; team_id?: string }) =>
    request<ModelAnalyticsResponse>("/models", params),

  getSessions: (params?: {
    start_date?: string;
    end_date?: string;
    developer_id?: string;
    page?: number;
    page_size?: number;
  }) => request<SessionListResponse>("/sessions", params),

  getDevices: () => request<DeviceListResponse>("/devices"),
};
