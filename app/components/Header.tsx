"use client";

import React from "react";
import { useRouter } from "next/navigation";
import { AUTH_KEY } from "./AuthGuard";

export default function Header() {
  const router = useRouter();

  function logout() {
    try {
      window.localStorage.removeItem(AUTH_KEY);
    } catch {
      // ignore
    }
    router.replace("/");
  }

  return (
    <header
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        gap: 12,
        flexWrap: "wrap",
        padding: "14px 0 10px",
        borderBottom: "1px solid #e5e7eb",
      }}
    >
      <div>
        <div style={{ margin: 0, fontSize: 22, fontWeight: 900, letterSpacing: 0.2 }}>
          Painel de Ferramentas - UPDE | HUSM
        </div>
        <div style={{ marginTop: 4, color: "#4b5563", fontSize: 13 }}>
          Versão 1.1.0 | 26/01/2026
        </div>
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <img
          src="/header_logos.png"
          alt="Logos institucionais"
          style={{ height: 44, width: "auto", display: "block" }}
        />

        <button className="btn btnGhost" onClick={logout} title="Sair" style={{ height: 36 }}>
          Sair
        </button>
      </div>
    </header>
  );
}
