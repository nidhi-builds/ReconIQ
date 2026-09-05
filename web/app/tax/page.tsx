import { PageHeader } from "@/components/page-header";
import { StatusPill } from "@/components/status-pill";
import { getTaxFindings, listRuns } from "@/lib/api";
import { formatCurrency, readableLabel } from "@/lib/view-model";

export default async function TaxPage() {
  const [run] = await listRuns();
  const findings = await getTaxFindings(run.id);
  const mismatches = findings.filter((item) => item.status === "mismatch");
  return <div className="page-wrap"><PageHeader eyebrow="Tax controls" title="GST & TDS review" description="Compare expected deductions and filing attributes against the synthetic 26AS reference." />
    <section className="tax-summary"><div><span>Reviewed</span><strong>{findings.length}</strong></div><div><span>Clear</span><strong>{findings.length - mismatches.length}</strong></div><div><span>Mismatches</span><strong>{mismatches.length}</strong></div></section>
    <section className="table-panel"><table><thead><tr><th>Reference</th><th>Section</th><th>Expected TDS</th><th>Actual TDS</th><th>Finding</th></tr></thead><tbody>{findings.map((item) => <tr key={item.id}><td><strong>{item.reference}</strong></td><td><code>{item.section}</code></td><td>{formatCurrency(item.expected_tds)}</td><td>{formatCurrency(item.actual_tds)}</td><td><StatusPill tone={item.status === "clear" ? "positive" : "warning"}>{item.mismatch_reason ? readableLabel(item.mismatch_reason) : "Clear"}</StatusPill></td></tr>)}</tbody></table></section>
  </div>;
}
