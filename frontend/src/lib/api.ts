/** Typed, thin client for our backend's /api/local/* endpoints.
 *  The backend holds the NuruXplore Bearer token server-side; the browser
 *  never sees it. Every failure arrives as { kind, message, status }.
 */

export type ErrorKind =
  | "auth"
  | "rate_limit"
  | "out_of_credits"
  | "network"
  | "generation_failed"
  | "http";

export class ApiError extends Error {
  kind: ErrorKind;
  status: number;
  constructor(kind: ErrorKind, message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}

export interface ApiInit {
  method?: string;
  headers?: HeadersInit;
  body?: unknown;
}

export async function api<T = unknown>(path: string, init: ApiInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  let body: BodyInit | undefined;
  if (init.body !== undefined) {
    if (init.body instanceof FormData) {
      body = init.body;
    } else {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(init.body);
    }
  }
  const res = await fetch(path, { method: init.method, headers, body });
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) {
    const kind: ErrorKind = data?.kind ?? "http";
    throw new ApiError(kind, data?.message ?? `HTTP ${res.status}`, res.status);
  }
  return data as T;
}

export const KIND_META: Record<ErrorKind, { title: string; variant: "destructive" | "warning" | "error" }> = {
  auth: { title: "Authentication failed — invalid or expired credentials.", variant: "destructive" },
  rate_limit: { title: "Rate limit reached. Wait a moment and try again.", variant: "warning" },
  out_of_credits: { title: "Insufficient credits for that action. The budget is fixed.", variant: "destructive" },
  network: { title: "Network error — the API did not respond.", variant: "error" },
  generation_failed: { title: "Document generation failed — your credits were refunded.", variant: "destructive" },
  http: { title: "Something went wrong talking to the API.", variant: "error" },
};

export function errorTitle(kind?: ErrorKind): string {
  if (!kind) return KIND_META.http.title;
  return (KIND_META[kind] ?? KIND_META.http).title;
}

/* ------------------------------ domain types ------------------------------ */

export interface Me {
  user: { name?: string; email?: string; credits_balance?: number };
  credits_balance?: number;
}

export interface Usage {
  calls?: number;
  tokens_in?: number;
  tokens_out?: number;
  cost_est?: number;
  duration_s?: number;
  steps?: { name: string; duration_s?: number }[];
}

export interface ChatResult {
  reply: string;
  credits_remaining?: number;
  agent?: "deepseek";
  usage?: Usage;
}

export interface Prefs {
  use_agents: boolean;
  agents_available: boolean;
  model?: string | null;
}

export interface AgentContent {
  title?: string;
  text: string;
  word_count?: number;
  agent?: "deepseek";
}

export interface ResearchProfile {
  title?: string;
  document_type?: string;
  background?: string[];
  methodology?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface OutlineResult {
  outline?: { title: string; subsections?: string[] }[];
  sections?: unknown[];
  message?: string;
}

export interface GenerationStatus {
  status: string;
  progress?: number;
  current_step?: string;
  steps?: { step: string; status: string; message?: string }[];
  word_count?: number;
  content_ready?: boolean;
  kind?: ErrorKind;
  message?: string;
}
