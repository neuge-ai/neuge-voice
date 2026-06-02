export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let apiBase = import.meta.env.VITE_AGENT_API_BASE ?? "http://127.0.0.1:8000";

export function getApiBase(): string {
  return apiBase;
}

export function setApiBase(base: string): void {
  apiBase = base;
}

export async function requestJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, init);
  if (!response.ok) {
    const body = await response.text();
    throw new ApiError(
      body || `HTTP ${response.status}`,
      response.status,
      body,
    );
  }
  return response.json() as Promise<T>;
}

export async function formatApiError(
  response: Response,
  fallback: string,
): Promise<string> {
  const raw = await response.text();
  if (!raw) {
    return `${fallback}: HTTP ${response.status}`;
  }
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown };
    if (parsed.detail && typeof parsed.detail === "object") {
      const detail = parsed.detail as {
        provider?: string;
        stage?: string;
        message?: string;
      };
      const source = [detail.provider, detail.stage].filter(Boolean).join("/");
      return `${fallback}${source ? ` (${source})` : ""}: ${detail.message ?? raw}`;
    }
    if (typeof parsed.detail === "string") {
      return `${fallback}: ${parsed.detail}`;
    }
  } catch {
    // Fall through to the raw server response.
  }
  return `${fallback}: ${raw}`;
}
