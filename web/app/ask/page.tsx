import { AskPanel } from "@/components/ask-panel";
import { PageHeader } from "@/components/page-header";
import { RunSelector } from "@/components/run-selector";
import { listRuns } from "@/lib/api";
import { selectRun } from "@/lib/view-model";

export default async function AskPage({ searchParams }: { searchParams: Promise<{ run?: string }> }) {
  const [{ run: requestedRun }, runs] = await Promise.all([searchParams, listRuns()]);
  const run = selectRun(runs, requestedRun);
  if (!run) throw new Error("No completed reconciliation runs are available.");
  return <div className="page-wrap narrow"><PageHeader eyebrow="Analyst assistant" title="Ask ReconIQ" description="Query the reconciled dataset in plain language. Answers remain scoped to the selected run." /><RunSelector runs={runs} selectedRunId={run.id} /><AskPanel runId={run.id} /></div>;
}
