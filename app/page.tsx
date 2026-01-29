// app/page.tsx
import React from "react";
import { doLogin } from "./login-actions";
import PasswordInput from "./components/PasswordInput";

export default function LoginPage({
  searchParams,
}: {
  searchParams?: { [key: string]: string | string[] | undefined };
}) {
  const error = typeof searchParams?.error === "string" ? searchParams?.error : "";

  const inputStyle: React.CSSProperties = {
    height: 40,
    borderRadius: 10,
    border: "1px solid #cbd5e1",
    padding: "0 12px",
    fontSize: 14,
    width: "100%",
  };

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
            <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: 0.2 }}>
              PAINEL DE FERRAMENTAS - UPDE
            </div>
            <div style={{ marginTop: 4, color: "#4b5563", fontSize: 13 }}>
              Ferramentas utilizadas pela UPDE | HUSM
            </div>
          </div>

          <img
            src="/header_logos.png"
            alt="Logos institucionais"
            style={{ height: 44, width: "auto", display: "block" }}
          />
        </div>

        <form action={doLogin} style={{ display: "grid", gap: 12 }}>
          <div style={{ display: "grid", gap: 6 }}>
            <label style={{ fontWeight: 800, fontSize: 13 }}>Usuário</label>
            <input
              name="login"
              autoComplete="username"
              placeholder="Digite seu usuário"
              style={inputStyle}
            />
          </div>

          <div style={{ display: "grid", gap: 6 }}>
            <label style={{ fontWeight: 800, fontSize: 13 }}>Senha</label>
            <PasswordInput
              name="password"
              autoComplete="current-password"
              placeholder="Digite sua senha"
              inputStyle={inputStyle}
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
              Usuário ou senha inválidos.
            </div>
          )}

          <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 6, flexWrap: "wrap" }}>
            <button type="submit" className="btn btnCta" style={{ height: 40, minWidth: 140 }}>
              Entrar
            </button>

            <div style={{ fontSize: 12, color: "#6b7280" }}>
              Acesso restrito — necessário login para visualizar as aplicações.
            </div>
          </div>

          <div style={{ marginTop: 6, fontSize: 12, color: "#6b7280" }}>
            Primeiro acesso: defina <b>ADMIN_USERNAME</b> e <b>ADMIN_PASSWORD</b> nas variáveis de ambiente para criar o
            usuário admin automaticamente.
          </div>
        </form>
      </div>
    </main>
  );
}
