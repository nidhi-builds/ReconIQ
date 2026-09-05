export function StatusPill({ tone, children }: { tone: "positive" | "warning" | "neutral"; children: React.ReactNode }) {
  return <span className={`status-pill ${tone}`}><span aria-hidden="true" />{children}</span>;
}
