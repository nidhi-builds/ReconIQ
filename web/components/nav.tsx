"use client";

import { BarChart3, FileCheck2, Landmark, MessageSquareText, ReceiptText } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const items = [
  { href: "/", label: "Overview", icon: BarChart3 },
  { href: "/reconciliations", label: "Reconciliations", icon: FileCheck2 },
  { href: "/tax", label: "Tax", icon: ReceiptText },
  { href: "/ask", label: "Ask", icon: MessageSquareText },
  { href: "/audit", label: "Audit", icon: Landmark },
];

export function Nav() {
  const pathname = usePathname();
  return <nav className="nav" aria-label="Primary navigation">
    {items.map(({ href, label, icon: Icon }) => {
      const active = href === "/" ? pathname === href : pathname.startsWith(href);
      return <Link className={active ? "nav-link active" : "nav-link"} href={href} key={href}>
        <Icon aria-hidden="true" size={18} strokeWidth={1.8} /><span>{label}</span>
      </Link>;
    })}
  </nav>;
}
