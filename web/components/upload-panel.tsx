"use client";

import { FormEvent, useRef, useState } from "react";
import { RotateCcw, Upload } from "lucide-react";
import { uploadRun } from "@/lib/api";

type Source = "ledger" | "settlement" | "bank" | "tax_26as";

export function UploadPanel() {
  const form = useRef<HTMLFormElement>(null);
  const [files, setFiles] = useState<Partial<Record<Source, File>>>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!files.ledger || !files.settlement || !files.bank) return;
    setLoading(true); setError("");
    try {
      const run = await uploadRun({
        ledger_csv: await files.ledger.text(),
        settlement_csv: await files.settlement.text(),
        bank_csv: await files.bank.text(),
        tax_26as_csv: files.tax_26as ? await files.tax_26as.text() : "",
      });
      window.location.assign(`/?run=${encodeURIComponent(run.id)}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Upload failed");
    } finally { setLoading(false); }
  }

  function resetFiles() {
    form.current?.reset();
    setFiles({});
    setError("");
  }

  return <form className="upload-panel" onSubmit={submit} ref={form}>
    <div><strong>Run your files</strong><span>Upload ledger, settlement, and bank CSVs. Tax 26AS is optional.</span></div>
    {(["ledger", "settlement", "bank", "tax_26as"] as Source[]).map((source) => <label key={source}>{source === "tax_26as" ? "Tax 26AS (optional)" : `${source} CSV`}<input type="file" accept=".csv,text/csv" required={source !== "tax_26as"} onChange={(event) => setFiles((items) => ({ ...items, [source]: event.target.files?.[0] }))} /></label>)}
    <button type="submit" disabled={loading || !files.ledger || !files.settlement || !files.bank}><Upload size={16} />{loading ? "Running..." : "Upload & run"}</button>
    <button className="secondary-action" type="button" disabled={loading || Object.keys(files).length === 0} onClick={resetFiles}><RotateCcw size={15} />Reset files</button>
    {error && <span className="inline-error">{error}</span>}
  </form>;
}
