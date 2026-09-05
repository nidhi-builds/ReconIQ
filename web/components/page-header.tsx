import { dataMode } from "@/lib/api";

export function PageHeader({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) {
  return <header className="page-header">
    <div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p className="lede">{description}</p></div>
    <span className={`mode ${dataMode}`}>{dataMode === "live" ? "Live API" : "Preview data"}</span>
  </header>;
}
