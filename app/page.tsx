"use client";

import React, { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

const AUTH_KEY = "upde_auth_v1";
const USER = "admin";
const PASS = "upde@2026";

function isAuthed(): boolean {
  try {
    return window.localStorage.getItem(AUTH_KEY) === "1";
  } catch {
    return false;
  }
}

export default function LoginPage() {
  const router = useRouter();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string>("");

  const canSubmit = useMemo(() => username.trim().length > 0 && password.length > 0, [username, password]);

  useEffect(() => {
    // Se já estiver autenticado, pula a tela de login.
    if (isAuthed()) router.replace("/precos");
  }, [router]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    if (username.trim() === USER && password === PASS) {
      try {
        window.localStorage.setItem(AUTH_KEY, "1");
      } catch {
        // Se o storage falhar, ainda assim tenta navegar (auth guard irá bloquear).
      }
      router.replace("/precos");
      return;
    }

    setError("Usuário ou senha inválidos.");
  }

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "24px 16px",
        background: "#ffffff",
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: 520,
          border: "1px solid #e5e7eb",
          borderRadius: 16,
          padding: 18,
          boxShadow: "0 12px 40px rgba(17, 24, 39, 0.08)",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 12,
            flexWrap: "wrap",
            paddingBottom: 12,
            borderBottom: "1px solid #e5e7eb",
            marginBottom: 14,
          }}
        >
          <div>
            <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: 0.2 }}>ANÁLISE DE PREÇOS - UPDE</div>
            <div style={{ marginTop: 4, color: "#4b5563", fontSize: 13 }}>
              Formação de preços de referência com base em pesquisa do ComprasGOV
            </div>
          </div>

          <img
            src="/header_logos.png"
            alt="Logos institucionais"
            style={{ height: 44, width: "auto", display: "block" }}
          />
        </div>

        <form onSubmit={handleSubmit} style={{ display: "grid", gap: 12 }}>
          <div style={{ display: "grid", gap: 6 }}>
            <label style={{ fontWeight: 800, fontSize: 13 }}>Usuário</label>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              placeholder="Digite seu usuário"
              style={{
                height: 40,
                borderRadius: 10,
                border: "1px solid #cbd5e1",
                padding: "0 12px",
                fontSize: 14,
              }}
            />
          </div>

          <div style={{ display: "grid", gap: 6 }}>
            <label style={{ fontWeight: 800, fontSize: 13 }}>Senha</label>
            <input
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
              autoComplete="current-password"
              placeholder="Digite sua senha"
              style={{
                height: 40,
                borderRadius: 10,
                border: "1px solid #cbd5e1",
                padding: "0 12px",
                fontSize: 14,
              }}
            />
          </div>

          {error && (
            <div
              style={{
                border: "1px solid #fecaca",
                background: "#fef2f2",
                color: "#991b1b",
                borderRadius: 10,
                padding: "10px 12px",
                fontSize: 13,
                fontWeight: 700,
              }}
            >
              {error}
            </div>
          )}

          <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 6, flexWrap: "wrap" }}>
            <button
              type="submit"
              className={`btn ${canSubmit ? "btnCta" : "btnPrimary"}`}
              disabled={!canSubmit}
              title={!canSubmit ? "Preencha usuário e senha" : "Entrar"}
              style={{ height: 40, minWidth: 140 }}
            >
              Entrar
            </button>

            <div style={{ fontSize: 12, color: "#6b7280" }}>
              Acesso restrito — necessário login para visualizar as aplicações.
            </div>
          </div>
        </form>
      </div>
    </main>
  );
}
