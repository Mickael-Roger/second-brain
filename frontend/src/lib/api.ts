// Thin fetch wrapper. All requests are credentialed (cookies) and JSON in/out.

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown, message?: string) {
    super(message ?? `Request failed with status ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  init: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...((init.headers as Record<string, string>) ?? {}),
  };
  let payload: BodyInit | undefined;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const resp = await fetch(path, {
    method,
    credentials: "include",
    headers,
    body: payload,
    ...init,
  });
  if (resp.status === 204) return undefined as T;
  if (!resp.ok) {
    let detail: unknown;
    try {
      detail = await resp.json();
    } catch {
      detail = await resp.text();
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.headers.get("content-type")?.includes("application/json")) {
    return (await resp.json()) as T;
  }
  return undefined as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  delete: <T = void>(path: string, body?: unknown) => request<T>("DELETE", path, body),
};

// ---- DTOs ----

export interface MeResponse {
  username: string;
}

export interface ChatSummary {
  id: string;
  title: string;
  module_id: string | null;
  model: string | null;
  created_at: string;
  updated_at: string;
  archived: boolean;
}

export type ContentBlock =
  | { type: "text"; text: string }
  | { type: "image"; mime: string; data: string }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
  | {
      type: "tool_result";
      tool_use_id: string;
      content: ContentBlock[];
      is_error: boolean;
    };

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: ContentBlock[];
}

export interface ChatDetail extends ChatSummary {
  messages: ChatMessage[];
}

export interface ProviderInfo {
  name: string;
  kind: string;
  models: string[];
  default_model: string;
  is_default: boolean;
}

// ---- Vault ----

export interface TreeEntry {
  path: string;
  type: "folder" | "file";
  depth: number;
}

export interface VaultBacklink {
  path: string;
  snippet: string;
}

export interface VaultNote {
  path: string;
  content: string;
  backlinks: VaultBacklink[];
}

export interface VaultSearchHit {
  path: string;
  line_number: number;
  snippet: string;
}

// ---- News ----

export interface NewsArticleSummary {
  id: string;
  source: string;
  feed_id: string | null;
  feed_title: string | null;
  feed_group: string | null;
  feed_favicon: string | null;
  title: string;
  published_at: string;
  is_read: boolean;
}

export interface NewsArticleDetail extends NewsArticleSummary {
  url: string | null;
  author: string | null;
  image_url: string | null;
  summary: string | null;
  raw_html: string | null;
}

export interface NewsFeedSummary {
  feed_id: string;
  feed_title: string;
  feed_group: string | null;
  favicon: string | null;
  total: number;
  unread: number;
}

// ---- Wiki review ----

export type WikiReviewRating = "uninteresting" | "soon" | "roughly" | "perfect";

export interface WikiReviewState {
  last_reviewed_at: string;
  last_rating: WikiReviewRating;
  next_due_at: string;
  excluded: boolean;
  review_count: number;
}

export interface WikiReviewStatus {
  has_reviewed_today: boolean;
  reviewed_today_count: number;
  excluded_count: number;
  total_in_state: number;
}

export interface WikiReviewNext {
  path: string;
  content: string;
  state: WikiReviewState | null;
}

