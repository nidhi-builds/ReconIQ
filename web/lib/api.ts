import type { PaginatedResults, QuestionResponse, ReconciliationRecord, RunSummary, TaxFinding } from "./types";
import { requireApiUrl } from "./view-model";

const API_URL = requireApiUrl(process.env.NEXT_PUBLIC_API_URL);

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}/api/v1${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`API request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export async function listRuns(): Promise<RunSummary[]> {
  return request<RunSummary[]>("/runs");
}

export async function getRun(runId: string): Promise<RunSummary> {
  return request<RunSummary>(`/runs/${runId}`);
}

export async function getResults(runId: string, status?: string): Promise<PaginatedResults> {
  return request<PaginatedResults>(`/runs/${runId}/results${status ? `?status=${status}` : ""}`);
}

export async function getResult(runId: string, resultId: string): Promise<ReconciliationRecord> {
  return request<ReconciliationRecord>(`/runs/${runId}/results/${resultId}`);
}

export async function getTaxFindings(runId: string): Promise<TaxFinding[]> {
  const page = await request<{ items: TaxFinding[] }>(`/runs/${runId}/tax-results`);
  return page.items;
}

export async function askQuestion(runId: string, question: string): Promise<QuestionResponse> {
  return request<QuestionResponse>(`/runs/${runId}/questions`, { method: "POST", body: JSON.stringify({ question }) });
}

export async function uploadRun(payload: {
  ledger_csv: string;
  settlement_csv: string;
  bank_csv: string;
  tax_26as_csv: string;
}): Promise<RunSummary> {
  return request<RunSummary>("/runs/upload", { method: "POST", body: JSON.stringify(payload) });
}
