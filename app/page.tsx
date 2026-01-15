"use client";

import React, { useMemo, useState } from "react";

type BusyMode = "none" | "parse" | "debug";

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState<BusyMode>("none");
  const [msg, setMsg] = useState<string>("");

  const canParse = useMemo(() => !!file && busy === "none", [file, busy]);
  const canDebug = useMemo(() => !!file && busy === "none", [file, busy]);

  async function postAndDownload(endpoint: "/api/parse" | "/api/debug", defaultFilename: string) {
    if (!file) return;

    const form = new FormData();
    form.append("file", file, file.name);

    const res = await fetch(endpoint, { method: "POST", body: form });
    const contentType = res.headers.get("content-type") || "";

    // Se veio texto (erro/aviso), mostra na tela
    if (!res.ok || contentType.includes("text/plain") || contentType.includes("application/json")) {
      const text = await res.text();
      throw new Error(text || "Falha ao processar.");
    }

    const blob = await res.blob();
    const cd = res.headers.get("content-disposition") || "";
    const match = cd.match(/filename="([^"]+)"/i);
    const filename = match?.[1] || defaultFilename;

    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  }

  async function onParse(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;

    setBusy("parse");
    setMsg("Processando PDF e gerando Excel…");

    try {
      const base = file.name.replace(/\.pdf$/i, "");
      await postAndDownload("/api/parse", `${base}_compoe_sim.xlsx`);
      setMsg("Concluído. Excel baixado.");
    } catch (err: any) {
      setMsg(err?.message || "Erro inesperado.");
    } finally {
      setBusy("none");
    }
  }

  async function onDebug() {
    if (!file) return;

    setBusy("debug");
    setMsg("Gerando dump.txt (debug do texto extraído)…");

    try {
      const base = file.name.replace(/\.pdf$/i, "");
      await postAndDownload("/api/debug", `${base}_dump.txt`);
      setMsg("Dump baixado. Me envie um trecho do arquivo dump.txt.");
    } catch (err: any) {
      setMsg(err?.message || "Erro inesperado.");
    } finally {
      setBusy("none");
    }
  }

  return (
    <main style={{ maxWidth: 860, margin: "40px auto", padding: 16, fontFamily: "system-ui, Arial" }}>
      <h1 style={{ marginBottom: 8 }}>Extrator de Cotação (PDF → Excel)</h1>
      <p style={{ marginTop: 0, color: "#444" }}>
        Envie o PDF e baixe o Excel somente com linhas onde <b>Compõe = Sim</b>. <br />
        Se o nome estiver vindo errado, use <b>Debug</b> para baixar um <code>dump.txt</code> com o texto extraído.
      </p>

      <form onSubmit={onParse} style={{ marginTop: 24, padding: 16, border: "1px solid #ddd", borderRadius: 12 }}>
        <label style={{ display: "block", fontWeight: 700, marginBottom: 8 }}>Arquivo PDF</label>

        <input
          type="file"
          accept="application/pdf"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
        />

        {file && (
          <div style={{ marginTop: 12, color: "#666" }}>
            Selecionado: <b>{file.name}</b> ({(file.size / (1024 * 1024)).toFixed(2)} MB)
          </div>
        )}

        <div style={{ marginTop: 16, display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <button
            type="submit"
            disabled={!canParse}
            style={{
              padding: "10px 14px",
              borderRadius: 10,
              border: "1px solid #ccc",
              background: canParse ? "white" : "#f2f2f2",
              cursor: canParse ? "pointer" : "not-allowed",
              fontWeight: 700,
            }}
          >
            {busy === "parse" ? "Processando…" : "Processar e baixar Excel"}
          </button>

          <button
            type="button"
            onClick={onDebug}
            disabled={!canDebug}
            style={{
              padding: "10px 14px",
              borderRadius: 10,
              border: "1px solid #ccc",
              background: canDebug ? "white" : "#f2f2f2",
              cursor: canDebug ? "pointer" : "not-allowed",
              fontWeight: 700,
            }}
          >
            {busy === "debug" ? "Gerando…" : "Debug (baixar dump.txt)"}
          </button>

          <span style={{ color: "#333" }}>{msg}</span>
        </div>

        <div style={{ marginTop: 14, fontSize: 13, color: "#666" }}>
          <b>Como usar o debug:</b> clique em “Debug”, baixe o <code>dump.txt</code>, e cole aqui o trecho que contém o header da tabela e
          as linhas onde o nome veio vazio/errado.
        </div>
      </form>
    </main>
  );
}
