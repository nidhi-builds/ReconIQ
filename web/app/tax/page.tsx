import { PageHeader } from "@/components/page-header";
import { StatusPill } from "@/components/status-pill";
import { RunSelector } from "@/components/run-selector";
import { getTaxFindings, listRuns } from "@/lib/api";
import { formatCurrency, readableLabel, selectRun, visibleRuns } from "@/lib/view-model";

export default async function TaxPage({ searchParams }: { searchParams: Promise<{ run?: string }> }) {
  const [{ run: requestedRun }, runs] = await Promise.all([searchParams, listRuns()]);
  const shownRuns = visibleRuns(runs);
  const run = selectRun(shownRuns, requestedRun);
  if (!run) throw new Error("No completed reconciliation runs are available.");
  const findings = await getTaxFindings(run.id);
  const mismatches = findings.filter((item) => item.status !== "CLEAR");
  return <div className="page-wrap"><PageHeader eyebrow="Tax controls" title="GST & TDS review" description="Compare expected deductions and filing attributes against the available 26AS reference." />
    <RunSelector runs={shownRuns} selectedRunId={run.id} />
    <section className="tax-summary"><div><span>Reviewed</span><strong>{findings.length}</strong></div><div><span>Clear</span><strong>{findings.length - mismatches.length}</strong></div><div><span>Mismatches</span><strong>{mismatches.length}</strong></div></section>
    {run.source_counts.tax_26as === 0 ? <section className="empty-state">No Tax 26AS document was uploaded for this run.</section> : <section className="table-panel"><table><thead><tr><th>Reference</th><th>Section</th><th>Expected TDS</th><th>Actual TDS</th><th>Finding</th></tr></thead><tbody>{findings.map((item) => <tr key={item.settlement_id}><td><strong>{item.ref_id ?? "Missing"}</strong></td><td><code>{item.tds_section}</code></td><td>{item.expected_tds === null ? "—" : formatCurrency(item.expected_tds)}</td><td>{formatCurrency(item.actual_tds)}</td><td><StatusPill tone={item.status === "CLEAR" ? "positive" : "warning"}>{item.mismatch_reason ? readableLabel(item.mismatch_reason) : readableLabel(item.status)}</StatusPill></td></tr>)}</tbody></table></section>}
  </div>;
}
