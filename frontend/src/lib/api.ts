import type {components} from "../types/api.generated";

export type DashboardDetail = components["schemas"]["DashboardDetail"];
export type DashboardSummary = components["schemas"]["DashboardSummary"];
export type DashboardQueryRequest = components["schemas"]["DashboardQueryRequest"];
export type DashboardQueryResponse = components["schemas"]["DashboardQueryResponse"];
export type QueryResult = components["schemas"]["QueryResult"];
export type WidgetSpec = components["schemas"]["WidgetSpec"];
export type AgentResponse = components["schemas"]["AgentResponse"];
export type ConversationView = components["schemas"]["ConversationView"];
export type ApprovalRequest = components["schemas"]["ApprovalRequest"];
export type ApprovalResponse = components["schemas"]["ApprovalResponse"];

export type AuthUser = {
  username: string;
  role: "builder" | "viewer";
  scope_profile: "ALL" | "CA" | "TX";
};

export async function login(username: string, password: string): Promise<AuthUser> {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    credentials: "same-origin",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({username, password}),
  });
  if (!response.ok) {
    throw new Error(response.status === 401 ? "Sai tên đăng nhập hoặc mật khẩu." : "Không thể đăng nhập.");
  }
  return (await response.json()) as AuthUser;
}

export async function currentUser(): Promise<AuthUser | null> {
  const response = await fetch("/api/auth/me", {cache: "no-store", credentials: "same-origin"});
  if (response.status === 401) return null;
  if (!response.ok) throw new Error("Không thể tải phiên đăng nhập.");
  return (await response.json()) as AuthUser;
}

export async function logout(): Promise<void> {
  const response = await fetch("/api/auth/logout", {
    method: "POST",
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error("Không thể đăng xuất.");
}

export class ApiError extends Error {
  constructor(public status: number, public code?: string) {
    super(status === 401 ? "Phiên đăng nhập đã hết hạn." : status === 409
      ? "Dashboard không tương thích với phiên bản dữ liệu hiện tại."
      : status === 404 ? "Không tìm thấy dashboard đã duyệt." : "Không thể tải dữ liệu. Vui lòng thử lại.");
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {cache: "no-store", credentials: "same-origin", ...options});
  if (!response.ok) {
    let code: string | undefined;
    try {
      const error = await response.json();
      if (typeof error?.detail?.code === "string") code = error.detail.code;
    } catch { /* Status remains available for non-JSON gateway failures. */ }
    throw new ApiError(response.status, code);
  }
  return await response.json() as T;
}

export function listDashboards(signal?: AbortSignal): Promise<DashboardSummary[]> {
  return request("/api/dashboards", {signal});
}

export function getDashboard(id: string, signal?: AbortSignal): Promise<DashboardDetail> {
  return request(`/api/dashboards/${encodeURIComponent(id)}`, {signal});
}

export function queryDashboard(id: string, body: DashboardQueryRequest, signal?: AbortSignal): Promise<DashboardQueryResponse> {
  return request(`/api/dashboards/${encodeURIComponent(id)}/query`, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body), signal,
  });
}

export function createConversation(dashboardId?: string, signal?: AbortSignal): Promise<ConversationView> {
  return request("/api/conversations", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(dashboardId ? {dashboard_id: dashboardId} : {}), signal});
}

export function getConversation(id: string, signal?: AbortSignal): Promise<ConversationView> {
  return request(`/api/conversations/${encodeURIComponent(id)}`, {signal});
}

export function sendMessage(id: string, message: string, revision: number, signal?: AbortSignal): Promise<AgentResponse> {
  return request(`/api/conversations/${encodeURIComponent(id)}/messages`, {
    method: "POST", headers: {"Content-Type": "application/json"}, signal,
    body: JSON.stringify({message, expected_revision: revision}),
  });
}

export function decideDraft(id: string, body: ApprovalRequest): Promise<ApprovalResponse> {
  return request(`/api/conversations/${encodeURIComponent(id)}/approval`, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  });
}
