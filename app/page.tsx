"use client";

import React, { useMemo, useState } from "react";

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string>("");

  const canSend = useMemo(() => !!file && !busy, [file, busy]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;

    setBusy(true);
    setMsg("Enviando e processando…");

    try {
      const form = new FormData();
      form.append("file", file, file.name);

      const res = await fetch("/api/parse", {
        method: "POST",
        body: form,
      });

      const contentType = res.headers.get("content-type") || "";

      // Caso o backend devolva texto (ex.: sem linhas "Sim" ou erro amigável)
      if (!res.ok || contentType.includes("text/plain") || contentType.includes("application/json")) {
        const text = await res.text();
        setMsg(text || "Falha ao processar.");
        return;
      }

      // Download do XLSX
      const blob = await res.blob();
      const cd = res.headers.get("content-disposition") || "";
      const match = cd.match(/filename="([^"]+)"/i);
      const filename = match?.[1] || (file.name.replace(/\.pdf$/i, "") + "_compoe_sim.xlsx");

      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);

      setMsg("Concluído. Download iniciado.");
    } catch (err: any) {
      setMsg(err?.message || "Erro inesperado.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main style={{ maxWidth: 820, margin: "40px auto", padding: 16, fontFamily: "system-ui, Arial" }}>
      <h1 style={{ marginBottom: 8 }}>Extrator de Cotação (PDF → Excel)</h1>
      <p style={{ marginTop: 0, color: "#444" }}>
        Envie o PDF e baixe o Excel somente com linhas onde <b>Compõe = Sim</b>.
      </p>

      <form onSubmit={onSubmit} style={{ marginTop: 24, padding: 16, border: "1px solid #ddd", borderRadius: 12 }}>
        <label style={{ display: "block", fontWeight: 600, marginBottom: 8 }}>Arquivo PDF</label>

        <input
          type="file"
          accept="application/pdf"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
        />

        <div style={{ marginTop: 16, display: "flex", gap: 12, alignItems: "center" }}>
          <button
            type="submit"
            disabled={!canSend}
            style={{
              padding: "10px 14px",
              borderRadius: 10,
              border: "1px solid #ccc",
              background: canSend ? "white" : "#f2f2f2",
              cursor: canSend ? "pointer" : "not-allowed",
              fontWeight: 600,
            }}
          >
            {busy ? "Processando…" : "Processar e baixar Excel"}
          </button>

          <span style={{ color: "#333" }}>{msg}</span>
        </div>

        {file && (
          <div style={{ marginTop: 12, color: "#666" }}>
            Selecionado: <b>{file.name}</b> ({(file.size / (1024 * 1024)).toFixed(2)} MB)
          </div>
        )}
      </form>

      <p style={{ marginTop: 18, color: "#666", fontSize: 13 }}>
        Uso interno. Dados públicos.
      </p>
    </main>
  );
}
