"use client";

import { ArrowUp, LoaderCircle } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import type { QuestionResponse, SourceCounts } from "@/lib/types";

export function AskPanel({ runId, sourceCounts }: { runId: string; sourceCounts: SourceCounts }) {
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<QuestionResponse[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    try {
      setHistory(JSON.parse(sessionStorage.getItem(`reconiq:ask:${runId}`) ?? "[]"));
    } catch { setHistory([]); }
  }, [runId]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!question.trim()) return;
    const submittedQuestion = question.trim();
    setLoading(true); setError("");
    try {
      const result = await fetch("/api/question", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ runId, question: submittedQuestion }) });
      if (!result.ok) throw new Error("Question service is unavailable");
      const answer = await result.json() as QuestionResponse;
      const next = [...history, answer];
      setHistory(next);
      sessionStorage.setItem(`reconiq:ask:${runId}`, JSON.stringify(next));
      setQuestion("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Question service is unavailable");
    } finally { setLoading(false); }
  }

  return <div className="ask-layout">
    <section className="ask-thread" aria-live="polite">
      <div className="ask-scope"><strong>Documents in this run</strong><span>Ledger ({sourceCounts.ledger}) · Settlement ({sourceCounts.settlements}) · Bank ({sourceCounts.bank}){sourceCounts.tax_26as ? ` · Tax 26AS (${sourceCounts.tax_26as})` : " · Tax 26AS not uploaded"}</span></div>
      <div className="assistant-answer"><span>RI</span><div><strong>ReconIQ</strong><p>Ask about matches, exceptions, amounts, or reconciliation methods in this run.</p></div></div>
      {history.map((response, answerIndex) => <div className="thread-turn" key={`${response.question}-${answerIndex}`}><div className="user-question">{response.question}</div><div className="assistant-answer"><span>RI</span><div><strong>Answer</strong><p>{response.answer}</p>{response.rows.length > 0 && <div className="answer-rows">{response.rows.map((row, index) => <code key={index}>{Object.entries(row).map(([key, value]) => `${key}: ${value}`).join(" · ")}</code>)}</div>}</div></div></div>)}
      {error && <div className="inline-error">{error}</div>}
    </section>
    <form className="ask-form" onSubmit={submit}>
      <label htmlFor="question">Question</label>
      <div><input id="question" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Which exceptions have the highest value?" /><button type="submit" disabled={loading || !question.trim()} title="Ask question">{loading ? <LoaderCircle className="spin" size={18} /> : <ArrowUp size={18} />}<span className="sr-only">Ask</span></button></div>
    </form>
  </div>;
}
