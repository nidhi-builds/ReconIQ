import { NextRequest, NextResponse } from "next/server";
import { askQuestion } from "@/lib/api";

export async function POST(request: NextRequest) {
  try {
    const { runId, question } = await request.json() as { runId?: string; question?: string };
    if (!runId || !question?.trim()) return NextResponse.json({ detail: "runId and question are required" }, { status: 400 });
    return NextResponse.json(await askQuestion(runId, question.trim()));
  } catch {
    return NextResponse.json({ detail: "Question service unavailable" }, { status: 503 });
  }
}
