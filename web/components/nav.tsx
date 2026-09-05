"use client";

import { BarChart3, FileCheck2, MessageSquareText, ReceiptText } from "lucide-react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { runHref } from "@/lib/view-model";

const items = [
  { href: "/", label: "Overview", icon: BarChart3 },
  { href: "/reconciliations", label: "Reconciliations", icon: FileCheck2 },
  { href: "/tax", label: "Tax", icon: ReceiptText },
  { href: "/ask", label: "Ask", icon: MessageSquareText },
];

export function Nav() {
  const pathname = usePathname();
  const runId = useSearchParams().get("run");

  return <nav className="nav" aria-label="Primary navigation">
    {items.map(({ href, label, icon: Icon }) => {
      const active = href === "/" ? pathname === href : pathname.startsWith(href);
      return <Link className={active ? "nav-link active" : "nav-link"} href={runId ? runHref(href, runId) : href} key={href}>
        <Icon aria-hidden="true" size={18} strokeWidth={1.8} /><span>{label}</span>
      </Link>;
    })}
  </nav>;
}
