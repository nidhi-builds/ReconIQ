"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { RunSummary } from "@/lib/types";

export function RunSelector({ runs, selectedRunId }: { runs: RunSummary[]; selectedRunId: string }) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();

  function select(runId: string) {
    const params = new URLSearchParams(searchParams.toString());
    params.set("run", runId);
    router.push(`${pathname}?${params}`);
  }

  return <label className="run-selector">
    <span>Dataset</span>
    <select value={selectedRunId} onChange={(event) => select(event.target.value)}>
      {runs.map((run) => <option key={run.id} value={run.id}>{run.name}</option>)}
    </select>
  </label>;
}
