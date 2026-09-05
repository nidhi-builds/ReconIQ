import type { LucideIcon } from "lucide-react";

export function MetricCard({ label, value, detail, icon: Icon }: { label: string; value: string; detail: string; icon: LucideIcon }) {
  return <article className="metric-card">
    <div className="metric-icon"><Icon size={18} aria-hidden="true" /></div>
    <p>{label}</p><strong>{value}</strong><small>{detail}</small>
  </article>;
}
