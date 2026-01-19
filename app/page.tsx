"use client";

import { useState } from "react";

export default function Page() {
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<string>("");

  async function postFile(endpoint: string, defaultName: string) {
    if (!file) {
      setStatus("Selecione um PDF primeiro.");
      return;
    }

    setStatus("Enviando arquivo...");

    const form = new FormData();
    form.append("file", file);

    let res: Response;
    try {
      res = await fetch(endpoint, {
        method: "POST",
        body: form,
      });
    } catch (e: any) {
      setStatus(`Falha de rede: ${e?.message || e}`);
      return;
    }

    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      setStatus(`Erro ${res.status}: ${txt || "Falha ao processar."}`);
      return;
    }

    // Se vier texto (ex: "Nenhuma linha encontrada..."), mostra em status
    const contentType = res.headers.get("content-type") || "";
    if (contentType.includes("text/plain")) {
      const txt = await res.text().catch(() => "");
      setStatus(txt || "Concluído.");
      return;
    }

    const blob = await res.blob();

    // tenta pegar filename do header
    const cd = res.headers.get("content-disposition") || "";
    const match = cd.match(/filename="([^"]+)"/i);
    const filename = match?.[1] ?? defaultName;

    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);

    setStatus("Download iniciado.");
  }

  return (
    <main style={{ maxWidth: 760, margin: "40px auto", padding: "0 16px" }}>
      <h1 style={{ fontSize: 24, fontWeight: 700 }}>Extrator Compras.gov.br</h1>

      <p style={{ marginTop: 8 }}>
        Envie o PDF do “Relatório de pesquisa de preço”. O sistema retorna:
        <br />
        <strong>ZIP</strong> com <strong>relatorio.xlsx</strong> + <strong>Memoria_de_Calculo.pdf</strong>.
      </p>

      <div style={{ marginTop: 20 }}>
        <input
          type="file"
          accept="application/pdf"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
      </div>

      <div style={{ display: "flex", gap: 12, marginTop: 16, flexWrap: "wrap" }}>
        <button
          onClick={() => postFile("/api/parse", "resultado_extracao.zip")}
          style={{
            padding: "10px 14px",
            borderRadius: 8,
            border: "1px solid #222",
            cursor: "pointer",
            fontWeight: 600,
          }}
        >
          Processar (ZIP)
        </button>

        <button
          onClick={() => postFile("/api/debug", "debug_audit.txt")}
          style={{
            padding: "10px 14px",
            borderRadius: 8,
            border: "1px solid #222",
            cursor: "pointer",
            fontWeight: 600,
          }}
        >
          Debug (TXT)
        </button>
      </div>

      {status && (
        <div
          style={{
            marginTop: 16,
            padding: 12,
            borderRadius: 8,
            border: "1px solid #ddd",
            whiteSpace: "pre-wrap",
          }}
        >
          {status}
        </div>
      )}

      <p style={{ marginTop: 20, opacity: 0.8 }}>
        Dica: se “Processar” falhar, use “Debug” para gerar um TXT e comparar o conteúdo extraído.
      </p>
    </main>
  );
}
