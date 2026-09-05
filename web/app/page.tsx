import { AlertTriangle, BadgeCheck, IndianRupee, TimerReset } from "lucide-react";
import { MetricCard } from "@/components/metric-card";
import { PageHeader } from "@/components/page-header";
import { StatusPill } from "@/components/status-pill";
import { listRuns } from "@/lib/api";
import { formatCurrency, formatPercent, readableLabel } from "@/lib/view-model";

export default async function OverviewPage() {
  const [run] = await listRuns();
  return <div className="page-wrap">
    <PageHeader eyebrow="Finance operations" title="Reconciliation overview" description="A clear view of what reconciled, what needs attention, and how each decision was made." />
    <section className="run-strip"><div><StatusPill tone="positive">Completed</StatusPill><strong>{run.name}</strong><span>{new Date(run.created_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}</span></div><div className="source-counts"><span>Ledger <b>{run.source_counts.ledger}</b></span><span>Settlement <b>{run.source_counts.settlements}</b></span><span>Bank <b>{run.source_counts.bank}</b></span></div></section>
    <section className="metric-grid">
      <MetricCard label="Match rate" value={formatPercent(run.summary.match_rate)} detail={`${run.summary.matched_records} records reconciled`} icon={BadgeCheck} />
      <MetricCard label="Matched value" value={formatCurrency(run.summary.matched_amount)} detail="Across confirmed groups" icon={IndianRupee} />
      <MetricCard label="Exceptions" value={String(run.summary.exception_records)} detail="Require review or source correction" icon={AlertTriangle} />
      <MetricCard label="LLM latency" value={`${(run.llm_metrics.average_latency_ms / 1000).toFixed(1)}s`} detail={`${run.llm_metrics.calls} reviewed candidates`} icon={TimerReset} />
    </section>
    <div className="overview-grid">
      <section className="panel"><div className="panel-heading"><div><p className="eyebrow">Resolution path</p><h2>Matches by stage</h2></div><span>{run.stage_counts.reduce((sum, item) => sum + item.count, 0)} groups</span></div><div className="stage-list">{run.stage_counts.map((item, index) => <div className="stage-row" key={item.stage}><div><span className="stage-number">0{index + 2}</span><strong>{item.label}</strong></div><div className="bar-track"><span style={{ width: `${item.count / Math.max(...run.stage_counts.map((stage) => stage.count)) * 100}%` }} /></div><b>{item.count}</b></div>)}</div></section>
      <section className="panel"><div className="panel-heading"><div><p className="eyebrow">Control quality</p><h2>Exception accuracy</h2></div><a href="/reconciliations">Review all</a></div><div className="exception-list">{run.exception_metrics.map((item) => <div key={item.category}><div><strong>{readableLabel(item.category)}</strong><small>{item.count} records</small></div><span>{item.precision === null ? "—" : formatPercent(item.precision)}</span></div>)}</div></section>
    </div>
  </div>;
}
