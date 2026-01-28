"use client";

import React, { useEffect, useState } from "react";

function getStorage(key: string): string {
  try {
    return localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}
function setStorage(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {}
}

export default function LoginPage() {
  const [usuario, setUsuario] = useState("");
  const [senha, setSenha] = useState("");
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // Se já estiver autenticado, redireciona automaticamente
    const token = getStorage("UPDE_AUTH");
    if (token === "1") {
      window.location.href = "/precos";
      return;
    }
    setReady(true);
  }, []);

  function doLogin() {
    setError("");

    // Autenticação simples por env (fallback para credenciais padrão se não definido)
    // OBS: Isso NÃO é segurança real — é apenas “barreira” de acesso.
    const USER = (process.env.NEXT_PUBLIC_LOGIN_USER || "admin").trim();
    const PASS = (process.env.NEXT_PUBLIC_LOGIN_PASS || "admin").trim();

    if (usuario.trim() === USER && senha === PASS) {
      setStorage("UPDE_AUTH", "1");
      window.location.href = "/precos";
      return;
    }
    setError("Usuário ou senha inválidos.");
  }

  if (!ready) return null;

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "24px 6px",
        background: "#ffffff",
      }}
    >
      <div
        style={{
          width: "min(520px, 100%)",
          border: "1px solid #e5e7eb",
          borderRadius: 14,
          padding: 18,
          boxShadow: "0 8px 28px rgba(0,0,0,0.06)",
        }}
      >
        <div style={{ fontSize: 20, fontWeight: 900, marginBottom: 6 }}>
          Análise de Preços - UPDE
        </div>
        <div style={{ color: "#6b7280", marginBottom: 14, fontSize: 14 }}>
          Formação de preços de referência com base em pesquisa do ComprasGOV
        </div>

        <label style={{ display: "block", fontWeight: 700, marginBottom: 6 }}>
          Usuário
        </label>
        <input
          value={usuario}
          onChange={(e) => setUsuario(e.target.value)}
          placeholder="Digite o usuário"
          style={{
            width: "100%",
            padding: "10px 12px",
            borderRadius: 10,
            border: "1px solid #d1d5db",
            marginBottom: 12,
            fontSize: 14,
          }}
        />

        <label style={{ display: "block", fontWeight: 700, marginBottom: 6 }}>
          Senha
        </label>
        <input
          value={senha}
          onChange={(e) => setSenha(e.target.value)}
          type="password"
          placeholder="Digite a senha"
          style={{
            width: "100%",
            padding: "10px 12px",
            borderRadius: 10,
            border: "1px solid #d1d5db",
            marginBottom: 12,
            fontSize: 14,
          }}
        />

        {error && (
          <div style={{ color: "#b91c1c", marginBottom: 10, fontWeight: 700 }}>
            {error}
          </div>
        )}

        <button
          onClick={doLogin}
          style={{
            width: "100%",
            padding: "10px 12px",
            borderRadius: 10,
            border: "1px solid #0f172a",
            background: "#111827",
            color: "white",
            fontWeight: 900,
            cursor: "pointer",
          }}
        >
          Entrar
        </button>

        <div style={{ marginTop: 12, fontSize: 12, color: "#6b7280" }}>
          * Acesso restrito (barreira simples).
        </div>
      </div>
    </main>
  );
}
