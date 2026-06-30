import { getToken } from '@/auth/token-store';
import { API_BASE_URL } from '@/config';

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

export interface ApiFetchOptions {
  method?: string;
  body?: unknown;
  /** Override the token read from secure storage. */
  token?: string;
  signal?: AbortSignal;
}

/**
 * Typed fetch wrapper for the FastAPI backend.
 * Sends X-API-Token (from opts or secure store) and JSON bodies when present.
 */
export async function apiFetch<T>(
  path: string,
  opts?: ApiFetchOptions,
): Promise<T> {
  const token = opts?.token ?? (await getToken());
  const url = `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;

  const headers: Record<string, string> = {};
  if (token) {
    headers['X-API-Token'] = token;
  }
  if (opts?.body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }

  const response = await fetch(url, {
    method: opts?.method ?? 'GET',
    headers,
    body: opts?.body !== undefined ? JSON.stringify(opts.body) : undefined,
    signal: opts?.signal,
  });

  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    throw new ApiError(`API error ${response.status}`, response.status, body);
  }

  // Some POST endpoints may return an empty body.
  const text = await response.text();
  if (!text) {
    return {} as T;
  }

  return JSON.parse(text) as T;
}
