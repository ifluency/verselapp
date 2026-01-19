"use client";

import { useState } from "react";

export default function Page() {
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<string>("");

  async function sendTo(endpoint: string, downloadName: string) {
    if (!file) {
      setStatus("Selecione um PDF primeiro.");
      return;
    }
    setStatus("Enviando...");

    const form = new FormData();
    form.append("file", file);

    const res = await fetch(endpoint, { method: "POST", body: form });
    if (!res.ok) {
      const msg = await res.text();
      setStatus(`Falha ao processar: ${msg}`);
      return;
    }

    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = downloadName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);

    setStatus("Concluído!");
  }

  return (
    <main style={{ maxWidth: 720, margin: "40px auto", padding: "0 16px" }}>
      <h1>Extrator Compras.gov.br</h1>
      <p>
        Envie um PDF e gere um .zip com Excel + PDF (Memória de Cálculo). Use o
        Debug para auditar cálculos.
      </p>

      <input
        type="file"
        accept="application/pdf"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
      />

      <div style={{ display: "flex", gap: 12, marginTop: 16, flexWrap: "wrap" }}>
        <button onClick={() => sendTo("/api/parse", "resultado.zip")}>
          Processar (ZIP)
        </button>

        <button onClick={() => sendTo("/api/debug", "debug_audit.txt")}>
          Debug (cálculos)
        </button>
      </div>

      {status && <p style={{ marginTop: 16 }}>{status}</p>}
    </main>
  );
}
