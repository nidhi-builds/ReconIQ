export type ResultStatusInput = {
  matched: boolean;
  exception_reason: string | null;
};

export function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 2,
  }).format(value);
}

export function formatPercent(value: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(value);
}

export function resultStatus(result: ResultStatusInput): "Matched" | "Exception" | "Pending" {
  if (result.matched) return "Matched";
  if (result.exception_reason) return "Exception";
  return "Pending";
}

export function readableLabel(value: string): string {
  return value.replaceAll("_", " ").toLowerCase().replace(/^./, (letter) => letter.toUpperCase());
}

export function requireApiUrl(value: string | undefined): string {
  if (!value) throw new Error("NEXT_PUBLIC_API_URL is required");
  return value.replace(/\/$/, "");
}

export function selectRun(runs: RunSummary[], requestedId?: string): RunSummary | undefined {
  return runs.find((run) => run.id === requestedId)
    ?? runs.find((run) => run.run_type === "holdout")
    ?? runs.find((run) => run.run_type === "design")
    ?? runs[0];
}

export function runHref(path: string, runId: string): string {
  return `${path}${path.includes("?") ? "&" : "?"}run=${encodeURIComponent(runId)}`;
}
import type { RunSummary } from "./types";
