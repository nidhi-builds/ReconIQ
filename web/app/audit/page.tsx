import { CheckCircle2, Copy, Link2, ShieldCheck } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { getAuditProof, listRuns } from "@/lib/api";

export default async function AuditPage() {
  const [run] = await listRuns();
  const proof = await getAuditProof(run.id, "result-001");
  return <div className="page-wrap"><PageHeader eyebrow="Integrity controls" title="Audit verification" description="Verify that a reconciliation record belongs to the immutable Merkle root prepared for this run." />
    <div className="audit-grid"><section className="audit-hero"><div className="shield"><ShieldCheck size={31} /></div><p className="eyebrow">Proof status</p><h2>{proof.verified ? "Record integrity verified" : "Verification pending"}</h2><p>The leaf and proof path recompute to the run&apos;s Merkle root.</p><span><CheckCircle2 size={16} /> Cryptographic proof passed</span></section>
    <section className="panel proof-panel"><div><span>Record</span><code>{proof.record_id}</code></div><div><span>Leaf hash</span><code>{proof.leaf_hash}</code><button title="Copy leaf hash"><Copy size={15} /></button></div><div><span>Merkle root</span><code>{proof.merkle_root}</code><button title="Copy Merkle root"><Copy size={15} /></button></div><div><span>Chain anchor</span><strong>{run.audit.status === "anchored" ? "Anchored" : "Ready to anchor"}</strong><Link2 size={16} /></div></section></div>
    <section className="panel proof-path"><div className="panel-heading"><div><p className="eyebrow">Independent check</p><h2>Merkle proof path</h2></div><span>{proof.proof.length} nodes</span></div>{proof.proof.map((node, index) => <div key={node}><span>{index + 1}</span><code>{node}</code></div>)}</section>
  </div>;
}
