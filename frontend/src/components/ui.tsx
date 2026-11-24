import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertCircle, CheckCircle2, CircleDot, Clock, FileCheck2, HelpCircle, Inbox, Search, XCircle, type LucideIcon,
} from "lucide-react";
import { getMe } from "../api";

/* ---------- Current user ---------- */
export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: getMe, staleTime: Infinity });
}

/* ---------- Status badge: colour + icon + label, never colour alone ---------- */
const STATUS: Record<string, { cls: string; icon: LucideIcon }> = {
  "Pending Review": { cls: "badge-info", icon: CircleDot },
  "In Review": { cls: "badge-info", icon: Search },
  "Info Needed": { cls: "badge-warn", icon: HelpCircle },
  "PO/Req Created": { cls: "badge-ok", icon: FileCheck2 },
  "PO Issued": { cls: "badge-ok", icon: FileCheck2 },
  "Contract Signed": { cls: "badge-ok", icon: FileCheck2 },
  Closed: { cls: "badge-neutral", icon: CheckCircle2 },
  Rejected: { cls: "badge-danger", icon: XCircle },
};

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { cls: "badge-neutral", icon: CircleDot };
  const Icon = s.icon;
  return (
    <span className={`badge ${s.cls}`}>
      <Icon size={13} aria-hidden /> {status}
    </span>
  );
}

export function EmailBadge({ status }: { status: string }) {
  if (status === "Sent") return <span className="badge badge-ok"><CheckCircle2 size={13} aria-hidden /> Emailed</span>;
  if (status === "Failed") return <span className="badge badge-danger"><AlertCircle size={13} aria-hidden /> Email failed</span>;
  return <span className="badge badge-neutral"><Clock size={13} aria-hidden /> Emailing…</span>;
}

export function CapitalTag({ capital }: { capital: boolean }) {
  return <span className={`tag ${capital ? "tag-capex" : "tag-opex"}`}>{capital ? "Capital" : "Non-capital"}</span>;
}

export function UrgentTag({ reason }: { reason: string | null }) {
  return <span className="badge badge-danger" title={reason ?? undefined}>Urgent</span>;
}

/* ---------- Layout bits ---------- */
export function Card({ title, subtitle, action, children, className = "" }: {
  title?: ReactNode; subtitle?: ReactNode; action?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {title && (
        <div className="card__head">
          <div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>
          {action}
        </div>
      )}
      <div className="card__body">{children}</div>
    </section>
  );
}

export function Empty({ title, children, icon: Icon = Inbox }: { title: string; children?: ReactNode; icon?: LucideIcon }) {
  return (
    <div className="empty">
      <div className="empty__icon"><Icon size={26} aria-hidden /></div>
      <h3>{title}</h3>
      {children}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  return (
    <div className="alert alert-danger" role="alert">
      <AlertCircle size={18} aria-hidden />
      <div>{error instanceof Error ? error.message : "Something went wrong."}</div>
    </div>
  );
}

export function Spinner({ dark = false }: { dark?: boolean }) {
  return <span className={`spinner ${dark ? "dark" : ""}`} role="status" aria-label="Loading" />;
}

export function PageLoader() {
  return <div className="center-screen"><Spinner dark /></div>;
}

/* ---------- Toasts ---------- */
type Toast = { id: number; text: string; error?: boolean };
const ToastCtx = createContext<(text: string, error?: boolean) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, error = false) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, error }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.error ? "error" : ""}`}>
            {t.error ? <AlertCircle size={18} aria-hidden /> : <CheckCircle2 size={18} aria-hidden />}
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
