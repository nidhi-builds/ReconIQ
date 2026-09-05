import { fixtureProof, fixtureResults, fixtureRun, fixtureTaxFindings } from "./fixtures";
import type { AuditProof, PaginatedResults, QuestionResponse, ReconciliationRecord, RunSummary, TaxFinding } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "");
export const dataMode = API_URL ? "live" : "fixture";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (!API_URL) throw new Error("API unavailable");
  const response = await fetch(`${API_URL}/api/v1${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`API request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export async function listRuns(): Promise<RunSummary[]> {
  return API_URL ? request<RunSummary[]>("/runs") : [fixtureRun];
}

export async function getRun(runId: string): Promise<RunSummary> {
  return API_URL ? request<RunSummary>(`/runs/${runId}`) : fixtureRun;
}

export async function getResults(runId: string, status?: string): Promise<PaginatedResults> {
  if (API_URL) return request<PaginatedResults>(`/runs/${runId}/results${status ? `?status=${status}` : ""}`);
  if (!status || status === "all") return fixtureResults;
  const items = fixtureResults.items.filter((item) => status === "matched" ? item.matched : !item.matched);
  return { ...fixtureResults, items, total: items.length };
}

export async function getResult(runId: string, resultId: string): Promise<ReconciliationRecord> {
  if (API_URL) return request<ReconciliationRecord>(`/runs/${runId}/results/${resultId}`);
  const result = fixtureResults.items.find((item) => item.id === resultId);
  if (!result) throw new Error("Result not found");
  return result;
}

export async function getTaxFindings(runId: string): Promise<TaxFinding[]> {
  if (!API_URL) return fixtureTaxFindings;
  const page = await request<{ items: TaxFinding[] }>(`/runs/${runId}/tax-results`);
  return page.items;
}

export async function askQuestion(runId: string, question: string): Promise<QuestionResponse> {
  if (API_URL) return request<QuestionResponse>(`/runs/${runId}/questions`, { method: "POST", body: JSON.stringify({ question }) });
  return {
    question,
    answer: "Five records remain unresolved in the current run, led by missing references and timing-lag exceptions.",
    rows: [{ category: "MISSING_REF_ID", count: 8 }, { category: "TIMING_LAG_EXCEEDED", count: 6 }],
  };
}

export async function getAuditProof(runId: string, resultId: string): Promise<AuditProof> {
  return API_URL ? request<AuditProof>(`/runs/${runId}/audit/${resultId}/verify`) : { ...fixtureProof, record_id: resultId };
}
