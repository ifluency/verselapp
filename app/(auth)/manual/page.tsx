"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

type ManualCard = {
  title: string;
  desc: string;
  href: string;
  badge?: string;
};

const CARDS: ManualCard[] = [
  {
    title: "Preços de Referência",
    desc: "Como enviar arquivos, revisar a prévia, ajustar valores e gerar o ZIP final com os PDFs.",
    href: "/manual/precos",
    badge: "Ferramenta",
  },
  {
    title: "Consulta CATMAT",
    desc: "Como colar a lista, consultar, interpretar Ativos/Inativos, copiar inativos e lidar com erros.",
    href: "/manual/catmat",
    badge: "Ferramenta",
  },
];

function Card({ c }: { c: ManualCard }) {
  return (
    <Link
      href={c.href}
      style={{
        textDecoration: "none",
        color: "inherit",
        border: "1px solid #e5e7eb",
        borderRadius: 14,
        background: "#ffffff",
        padding: 16,
        display: "grid",
        gap: 10,
        boxShadow: "0 12px 26px rgba(17, 24, 39, 0.06)",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "start" }}>
        <div style={{ fontSize: 15, fontWeight: 900, color: "#111827", lineHeight: 1.2 }}>{c.title}</div>
        {!!c.badge && (
          <div
            style={{
              fontSize: 11,
              fontWeight: 800,
              color: "#111827",
              border: "1px solid #e5e7eb",
              background: "#f9fafb",
              padding: "4px 8px",
              borderRadius: 999,
              whiteSpace: "nowrap",
            }}
          >
            {c.badge}
          </div>
        )}
      </div>
      <div style={{ color: "#374151", fontSize: 13, lineHeight: 1.55 }}>{c.desc}</div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginTop: 2,
          color: "#111827",
          fontSize: 13,
          fontWeight: 800,
        }}
      >
        Abrir manual
        <span aria-hidden="true">→</span>
      </div>
    </Link>
  );
}

export default function ManualIndexPage() {
  const pathname = usePathname();

  return (
    <main style={{ margin: "12px 0 0", padding: "0 0 110px" }}>
      <div style={{ marginTop: 4, marginBottom: 10 }}>
        <div style={{ fontSize: 18, fontWeight: 900, color: "#111827" }}>Manual de Utilização</div>
        <div style={{ marginTop: 4, fontSize: 13, color: "#4b5563", lineHeight: 1.5 }}>
          Selecione uma funcionalidade abaixo para abrir o passo a passo.
          <span style={{ display: "block", marginTop: 4 }}>
            Dica: você pode voltar para esta lista a qualquer momento em <code style={{ fontFamily: "inherit" }}>/manual</code>.
          </span>
        </div>
      </div>

      <div style={{ display: "grid", gap: 12 }}>
        <div
          style={{
            border: "1px solid #e5e7eb",
            borderRadius: 14,
            background: "#ffffff",
            padding: 16,
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 900, color: "#111827" }}>Acesso e navegação</div>
          <div style={{ marginTop: 8, color: "#374151", fontSize: 13, lineHeight: 1.55 }}>
            <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
              <li style={{ margin: "6px 0" }}>
                A página inicial (<code style={{ fontFamily: "inherit" }}>/</code>) é o login. Após autenticar, você é direcionado para <strong>Preços de Referência</strong>.
              </li>
              <li style={{ margin: "6px 0" }}>
                Nas páginas internas, use o ícone no canto superior esquerdo para abrir o menu e alternar entre as ferramentas.
              </li>
              <li style={{ margin: "6px 0" }}>
                Para encerrar a sessão, utilize o botão <strong>Sair</strong> no topo.
              </li>
              <li style={{ margin: "6px 0" }}>
                Você está em: <code style={{ fontFamily: "inherit" }}>{pathname}</code>
              </li>
            </ul>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
          {CARDS.map((c) => (
            <Card key={c.href} c={c} />
          ))}
        </div>
      </div>
    </main>
  );
}
