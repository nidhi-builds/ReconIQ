"use client";

import { RefreshCw, TriangleAlert } from "lucide-react";

export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return <div className="page-wrap centered-state"><TriangleAlert size={28} /><h1>Couldn&apos;t load this view</h1><p>The ReconIQ API may be unavailable. Check the API URL and try again.</p><button onClick={reset}><RefreshCw size={17} />Retry</button></div>;
}
