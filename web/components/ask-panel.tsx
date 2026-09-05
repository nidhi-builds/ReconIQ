"use client";

import { ArrowUp, LoaderCircle } from "lucide-react";
import { FormEvent, useState } from "react";
import type { QuestionResponse } from "@/lib/types";

export function AskPanel({ runId }: { runId: string }) {
  const [question, setQuestion] = useState("");
  const [response, setResponse] = useState<QuestionResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!question.trim()) return;
    setLoading(true); setError("");
    try {
      const result = await fetch("/api/question", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ runId, question }) });
      if (!result.ok) throw new Error("Question service is unavailable");
      setResponse(await result.json());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Question service is unavailable");
    } finally { setLoading(false); }
  }

  return <div className="ask-layout">
    <section className="ask-thread" aria-live="polite">
      <div className="assistant-answer"><span>RI</span><div><strong>ReconIQ</strong><p>Ask about matches, exceptions, amounts, or reconciliation methods in this run.</p></div></div>
      {response && <><div className="user-question">{response.question}</div><div className="assistant-answer"><span>RI</span><div><strong>Answer</strong><p>{response.answer}</p>{response.rows.length > 0 && <div className="answer-rows">{response.rows.map((row, index) => <code key={index}>{Object.entries(row).map(([key, value]) => `${key}: ${value}`).join(" · ")}</code>)}</div>}</div></div></>}
      {error && <div className="inline-error">{error}</div>}
    </section>
    <form className="ask-form" onSubmit={submit}>
      <label htmlFor="question">Question</label>
      <div><input id="question" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Which exceptions have the highest value?" /><button type="submit" disabled={loading || !question.trim()} title="Ask question">{loading ? <LoaderCircle className="spin" size={18} /> : <ArrowUp size={18} />}<span className="sr-only">Ask</span></button></div>
    </form>
  </div>;
}
