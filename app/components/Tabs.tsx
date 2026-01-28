"use client";

import React, { useMemo, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

type ToolLink = { label: string; href: string; desc?: string };

const MAIN_TOOLS: ToolLink[] = [
  { label: "Preços de Referência", href: "/precos", desc: "Geração dos Relatórios de Preços de Referência" },
  { label: "Consulta CATMAT", href: "/catmat", desc: "Busca por CATMATs inativos" },
  { label: "Arquivamentos", href: "/arquivamentos", desc: "Histórico de arquivamentos (R2)" },
];

const MANUAL_TOOL: ToolLink = {
  label: "Manual de Utilização",
  href: "/manual",
  desc: "Orientações de uso das ferramentas",
};

const ALL_TOOLS: ToolLink[] = [...MAIN_TOOLS, MANUAL_TOOL];

function IconMenu({ size = 18 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path d="M4 6h16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M4 12h16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M4 18h16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function IconClose({ size = 18 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path d="M6 6l12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

/**
 * Mantive o nome do componente como "Tabs" para evitar alterar imports.
 * Agora ele funciona como um menu flutuante (hambúrguer) + gaveta lateral esquerda.
 */
export default function Tabs() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  const activeLabel = useMemo(() => {
    const found = ALL_TOOLS.find((t) => t.href === pathname);
    return found?.label || "Ferramentas";
  }, [pathname]);

  return (
    <>
      {/* Botão flutuante (canto superior esquerdo) */}
      <button
        type="button"
        className="btn"
        onClick={() => setOpen(true)}
        aria-label="Abrir menu"
        title="Abrir menu"
        style={{
          position: "fixed",
          top: 14,
          left: 14,
          height: 40,
          width: 40,
          padding: 0,
          borderRadius: 12,
          zIndex: 60,
          boxShadow: "0 10px 26px rgba(17, 24, 39, 0.10)",
          background: "#ffffff",
        }}
      >
        <IconMenu />
      </button>

      {/* Overlay */}
      {open && (
        <div
          role="presentation"
          onClick={() => setOpen(false)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.35)",
            zIndex: 59,
          }}
        />
      )}

      {/* Drawer lateral */}
      <aside
        aria-label="Menu de ferramentas"
        style={{
          position: "fixed",
          top: 0,
          left: 0,
          height: "100vh",
          width: 290,
          background: "#ffffff",
          borderRight: "1px solid #e5e7eb",
          boxShadow: "12px 0 40px rgba(17, 24, 39, 0.10)",
          transform: open ? "translateX(0)" : "translateX(-105%)",
          transition: "transform 160ms ease",
          zIndex: 61,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 10,
            padding: "14px 14px 10px",
            borderBottom: "1px solid #e5e7eb",
          }}
        >
          <div style={{ display: "grid", gap: 2 }}>
            <div style={{ fontWeight: 900, letterSpacing: 0.2 }}>Ferramentas UPDE</div>
            <div style={{ fontSize: 12, color: "#4b5563" }}>Atual: {activeLabel}</div>
          </div>
          <button
            type="button"
            className="btn btnGhost"
            onClick={() => setOpen(false)}
            aria-label="Fechar menu"
            title="Fechar menu"
            style={{ height: 36, width: 36, padding: 0, borderRadius: 10 }}
          >
            <IconClose />
          </button>
        </div>

        <nav style={{ padding: 12, display: "grid", gap: 8 }}>
          {MAIN_TOOLS.map((t) => {
            const active = pathname === t.href;
            return (
              <Link
                key={t.href}
                href={t.href}
                onClick={() => setOpen(false)}
                style={{
                  textDecoration: "none",
                  border: "1px solid " + (active ? "#111827" : "#e5e7eb"),
                  borderRadius: 12,
                  padding: "10px 12px",
                  background: active ? "#111827" : "#ffffff",
                  color: active ? "#ffffff" : "#111827",
                  display: "grid",
                  gap: 4,
                }}
              >
                <div style={{ fontWeight: 900 }}>{t.label}</div>
                {!!t.desc && (
                  <div style={{ fontSize: 12, opacity: active ? 0.9 : 0.75 }}>{t.desc}</div>
                )}
              </Link>
            );
          })}
        </nav>

        <div style={{ marginTop: "auto", padding: 14, borderTop: "1px solid #e5e7eb" }}>
          {/* Manual fixo no canto inferior, acima do texto de ajuda */}
          <div style={{ marginBottom: 10 }}>
            <Link
              href={MANUAL_TOOL.href}
              onClick={() => setOpen(false)}
              style={{
                textDecoration: "none",
                border: "1px solid " + (pathname === MANUAL_TOOL.href ? "#111827" : "#e5e7eb"),
                borderRadius: 12,
                padding: "10px 12px",
                background: pathname === MANUAL_TOOL.href ? "#111827" : "#ffffff",
                color: pathname === MANUAL_TOOL.href ? "#ffffff" : "#111827",
                display: "grid",
                gap: 4,
              }}
            >
              <div style={{ fontWeight: 900 }}>{MANUAL_TOOL.label}</div>
              {!!MANUAL_TOOL.desc && (
                <div style={{ fontSize: 12, opacity: pathname === MANUAL_TOOL.href ? 0.9 : 0.75 }}>
                  {MANUAL_TOOL.desc}
                </div>
              )}
            </Link>
          </div>

          <div style={{ fontSize: 12, color: "#6b7280" }}>Navegue pelas ferramentas através deste menu.</div>
        </div>
      </aside>
    </>
  );
}
