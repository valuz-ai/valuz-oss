import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setAnalyticsApiBase = (url: string): void => {
  _apiBase = url;
};

export interface DailyModelUsage {
  date: string;
  request_count: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  total_tokens: number;
}

export interface ModelUsage {
  model: string;
  total_requests: number;
  total_tokens: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_read_tokens: number;
  total_cache_write_tokens: number;
  daily: DailyModelUsage[];
}

export interface UsageResponse {
  year: number;
  month: number;
  total_tokens: number;
  total_requests: number;
  models: string[];
  overview: Array<Record<string, unknown>>;
  by_model: ModelUsage[];
}

const fetchJson = createFetchJson(() => _apiBase);

export const analyticsApi = {
  getUsage(year: number, month: number): Promise<UsageResponse> {
    return fetchJson(`/v1/analytics/usage?year=${year}&month=${month}`);
  },
};
