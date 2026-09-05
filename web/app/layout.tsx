import type { Metadata } from "next";
import { CircleDollarSign } from "lucide-react";
import { Nav } from "@/components/nav";
import "./globals.css";

export const metadata: Metadata = { title: "ReconIQ", description: "Multi-source finance reconciliation workspace" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><div className="app-shell">
    <aside className="sidebar">
      <LinkBrand />
      <Nav />
      <div className="workspace"><span>Workspace</span><strong>Razorpay Buildathon</strong><small>Design environment</small></div>
    </aside>
    <main>{children}</main>
  </div></body></html>;
}

function LinkBrand() {
  return <a className="brand" href="/"><span><CircleDollarSign size={22} /></span><strong>ReconIQ</strong></a>;
}
