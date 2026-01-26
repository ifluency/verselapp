"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

type Tab = { label: string; href: string };

const TABS: Tab[] = [
  { label: "Preços de Referência", href: "/precos" },
  { label: "Consulta CATMAT", href: "/catmat" },
];

export default function Tabs() {
  const pathname = usePathname();

  return (
    <nav style={{ padding: "10px 0 0" }} aria-label="Navegação">
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {TABS.map((t) => {
          const active = pathname === t.href;
          return (
            <Link
              key={t.href}
              href={t.href}
              className="btn"
              style={{
                height: 34,
                borderRadius: 999,
                borderColor: active ? "#111827" : "#cbd5e1",
                background: active ? "#111827" : "#ffffff",
                color: active ? "#ffffff" : "#111827",
              }}
            >
              {t.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
