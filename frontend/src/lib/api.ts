import type {
  AuthResponse,
  CaseListResponse,
  CitationFeedback,
  LearningEvent,
  PlayerAssistResponse,
  PlayerRequest,
  SandboxState,
  SkillCardDetail,
  SkillCardSummary,
  StatusResponse,
  TeachingReport,
} from "./types";

const API_BASE = "/api";

function getToken(): string | null {
  return localStorage.getItem("lw.token");
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem("lw.token", token);
  else localStorage.removeItem("lw.token");
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers ?? {});
  if (!headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  async status(): Promise<StatusResponse> {
    return request<StatusResponse>("/status");
  },

  async register(email: string, password: string): Promise<AuthResponse> {
    return request<AuthResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  },

  async login(email: string, password: string): Promise<AuthResponse> {
    return request<AuthResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  },

  async me(): Promise<{ id: string; email: string }> {
    return request("/auth/me");
  },

  async getSandbox(): Promise<SandboxState> {
    return request<SandboxState>("/sandbox");
  },

  async ensureSandbox(): Promise<SandboxState> {
    return request<SandboxState>("/sandbox/ensure", { method: "POST" });
  },

  async listCases(): Promise<CaseListResponse> {
    return request<CaseListResponse>("/sandbox/cases");
  },

  async startSandbox(caseId: string): Promise<SandboxState> {
    return request<SandboxState>("/sandbox/start", {
      method: "POST",
      body: JSON.stringify({ case_id: caseId }),
    });
  },

  async pauseSandbox(): Promise<SandboxState> {
    return request<SandboxState>("/sandbox/pause", { method: "POST" });
  },

  async restartSandbox(): Promise<SandboxState> {
    return request<SandboxState>("/sandbox/restart", { method: "POST" });
  },

  async playerRuntime(caseId?: string): Promise<{
    player_mode: string;
    enabled: boolean;
    pending: PlayerRequest[];
    count: number;
  }> {
    const qs = caseId ? `?case_id=${encodeURIComponent(caseId)}` : "";
    return request(`/sandbox/player-lawyer/runtime${qs}`);
  },

  async playerRespond(
    requestId: string,
    message: string,
    extras: {
      original_message?: string;
      polished_message?: string;
      hint_ids?: string[];
      used_ai_polish?: boolean;
    } = {},
  ): Promise<{
    success: boolean;
    request: PlayerRequest;
    citation_feedback?: CitationFeedback | null;
  }> {
    return request("/sandbox/player-lawyer/respond", {
      method: "POST",
      body: JSON.stringify({
        request_id: requestId,
        message,
        ...extras,
      }),
    });
  },

  async playerDraft(
    requestId: string,
    hintIds: string[] = [],
  ): Promise<PlayerAssistResponse> {
    return request("/sandbox/player-lawyer/draft-response", {
      method: "POST",
      body: JSON.stringify({ request_id: requestId, hint_ids: hintIds }),
    });
  },

  async playerPolish(
    requestId: string,
    message: string,
  ): Promise<PlayerAssistResponse> {
    return request("/sandbox/player-lawyer/polish-response", {
      method: "POST",
      body: JSON.stringify({ request_id: requestId, original_message: message }),
    });
  },

  // ── Teaching ──────────────────────────────────────────────────────

  /** 单阶段批阅结果；404 = 尚未批阅（轮询用，不抛错） */
  async teachingEvent(caseId: string, stage: string): Promise<LearningEvent | null> {
    try {
      return await request<LearningEvent>(
        `/teaching/event/${encodeURIComponent(caseId)}/${encodeURIComponent(stage)}`,
      );
    } catch (err) {
      if (err instanceof Error && err.message.startsWith("404")) return null;
      throw err;
    }
  },

  async teachingReport(studentId: string): Promise<TeachingReport> {
    return request<TeachingReport>(
      `/teaching/report/${encodeURIComponent(studentId)}`,
    );
  },

  async skillCards(studentId: string): Promise<SkillCardSummary[]> {
    const payload = await request<{ student_id: string; cards: SkillCardSummary[] }>(
      `/teaching/skill-cards/${encodeURIComponent(studentId)}`,
    );
    return payload.cards ?? [];
  },

  async skillCardDetail(studentId: string, slug: string): Promise<SkillCardDetail> {
    return request<SkillCardDetail>(
      `/teaching/skill-cards/${encodeURIComponent(studentId)}/${encodeURIComponent(slug)}`,
    );
  },
};
