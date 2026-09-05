import { AskPanel } from "@/components/ask-panel";
import { PageHeader } from "@/components/page-header";
import { listRuns } from "@/lib/api";

export default async function AskPage() {
  const [run] = await listRuns();
  return <div className="page-wrap narrow"><PageHeader eyebrow="Analyst assistant" title="Ask ReconIQ" description="Query the reconciled dataset in plain language. Answers remain scoped to the selected run." /><AskPanel runId={run.id} /></div>;
}
