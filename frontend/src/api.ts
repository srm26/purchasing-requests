// Typed API client. Every server call goes through `api()` so auth and errors are handled once.

export type Me = { email: string; name: string; is_admin: boolean; demo_mode: boolean; email_live: boolean };
export type Option = { code: string; label: string };
export type Lookups = {
  approvers: Option[];
  urgent_reasons: string[];
  statuses: string[];
};

export type RequestSummary = {
  request_number: string;
  submitted_at: string;
  requester_name: string;
  requester_email: string;
  supplier_name: string;
  project_number: string;
  cost_center: string;
  is_capital_project: boolean;
  amount: number;
  start_date: string;
  is_urgent: boolean;
  urgent_reason: string | null;
  status: string;
  email_status: "Pending" | "Sent" | "Failed";
  po_number: string | null;
};

export type RequestDetail = RequestSummary & {
  approver_email: string;
  approver_name: string;
  po_recipients: string | null;
  is_po_extension: boolean;
  previous_po: string | null;
  capital_project_number: string | null;
  capital_cost_center: string | null;
  legal_review: boolean | null;
  end_date: string;
  agreement_signed: boolean;
  signature_date: string | null;
  description: string | null;
  email_error: string | null;
  email_sent_at: string | null;
  admin_notes: string | null;
  po_issued_at: string | null;
  po_issued_by: string | null;
  po_email_status: "Pending" | "Sent" | "Failed" | null;
  po_email_error: string | null;
  po_email_sent_at: string | null;
  updated_at: string;
  attachments: { id: number; file_name: string; size_bytes: number; category: "agreement" | "supporting" | "po"; uploaded_at: string }[];
  history: { old_status: string | null; new_status: string; changed_by: string; changed_at: string; note: string | null }[];
};

export type LookupStatus = {
  counts: Record<string, number>;
  sheets: Record<string, string>;
  refreshes: { started_at: string; ok: boolean; source: string; message: string; triggered_by: string }[];
};

export type Recipients = { to: string; cc: string; high_value_cc: string; source: "admin" | "app setting"; updated_by: string | null; updated_at: string | null };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(path, { ...init, headers, credentials: "same-origin" });

  if (res.status === 401) {
    // Easy Auth session expired: send the user through SSO and back to this page.
    const back = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.href = `/.auth/login/aad?post_login_redirect_uri=${back}`;
    throw new ApiError(401, "Signing you in…");
  }
  const isJson = res.headers.get("content-type")?.includes("application/json");
  const body = isJson ? await res.json() : null;
  if (!res.ok) {
    const detail = body?.detail;
    const msg = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : res.statusText;
    throw new ApiError(res.status, msg || "Request failed");
  }
  if (!isJson) throw new ApiError(res.status, "Unexpected response from server");
  return body as T;
}

export const getMe = () => api<Me>("/api/me");
export const getLookups = () => api<Lookups>("/api/lookups");
export const listRequests = (scope: "mine" | "all") => api<RequestSummary[]>(`/api/requests?scope=${scope}`);
export const getRequest = (n: string) => api<RequestDetail>(`/api/requests/${encodeURIComponent(n)}`);
export const submitRequest = (payload: object, agreement: File[], supporting: File[]) => {
  const fd = new FormData();
  fd.append("payload", JSON.stringify(payload));
  agreement.forEach((f) => fd.append("agreement_files", f));
  supporting.forEach((f) => fd.append("supporting_files", f));
  return api<RequestDetail>("/api/requests", { method: "POST", body: fd });
};
export const updateStatus = (n: string, status: string, note: string) =>
  api<RequestDetail>(`/api/requests/${encodeURIComponent(n)}/status`, { method: "PATCH", body: JSON.stringify({ status, note: note || null }) });
export const updateNotes = (n: string, admin_notes: string) =>
  api<RequestDetail>(`/api/requests/${encodeURIComponent(n)}/notes`, { method: "PUT", body: JSON.stringify({ admin_notes }) });
export const resendEmail = (n: string) => api<RequestDetail>(`/api/requests/${encodeURIComponent(n)}/resend-email`, { method: "POST" });
export const issuePo = (n: string, po_number: string, note: string, file: File) => {
  const fd = new FormData();
  fd.append("po_number", po_number);
  fd.append("note", note);
  fd.append("file", file);
  return api<RequestDetail>(`/api/requests/${encodeURIComponent(n)}/po`, { method: "POST", body: fd });
};
export const resendPoEmail = (n: string) => api<RequestDetail>(`/api/requests/${encodeURIComponent(n)}/po/resend-email`, { method: "POST" });
export const getRecipients = () => api<Recipients>("/api/admin/recipients");
export const saveRecipients = (to: string, cc: string, high_value_cc: string) =>
  api<Recipients>("/api/admin/recipients", { method: "PUT", body: JSON.stringify({ to, cc, high_value_cc }) });
export const getLookupStatus = () => api<LookupStatus>("/api/admin/lookups");
export const refreshLookups = () => api<LookupStatus>("/api/admin/lookups/refresh", { method: "POST" });
export const attachmentUrl = (n: string, id: number) => `/api/requests/${encodeURIComponent(n)}/attachments/${id}`;
