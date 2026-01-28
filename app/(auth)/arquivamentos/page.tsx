"use client";

import React, { useEffect, useMemo, useState } from "react";

type Row = {
  numero_lista: string;
  nome_lista: string | null;
  responsavel: string | null;
  processo_sei: string | null;
  salvo_em: string | null;
  ultima_edicao_em: string | null;
  latest_run_id: string | null;
  tamanho_bytes: number | null;
};

function fmtDate(s?: string | null) {
  if (!s) return "";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return String(s);
  return d.toLocaleString("pt-BR");
}

function fmtBytes(n?: number | null) {
  if (!n || n <= 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : 2)} ${units[i]}`;
}

function IconDownload() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M12 3v10m0 0l4-4m-4 4l-4-4M4 17v3h16v-3"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function IconPencil() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 20h9" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path
        d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4L16.5 3.5z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function ArquivamentosPage() {
  const [items, setItems] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<string>("");
  const [filtroLista, setFiltroLista] = useState<string>("");

  async function load() {
    setLoading(true);
    setStatus("");
    try {
      const qs = filtroLista ? `?lista=${encodeURIComponent(filtroLista)}` : "";
      const res = await fetch(`/api/archive_runs${qs}`);
      const data = await res.json();
      if (!res.ok) {
        setStatus(data?.error ? String(data.error) : "Falha ao carregar.");
        return;
      }
      setItems((data.items || []) as Row[]);
    } catch (e: any) {
      setStatus(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const rows = useMemo(() => items || [], [items]);

  async function presignAndDownload(runId: string) {
    const ok = window.confirm("Deseja mesmo baixar o .zip arquivado?");
    if (!ok) return;

    setStatus("Gerando link de download...");
    try {
      const res = await fetch(`/api/archive_presign?run_id=${encodeURIComponent(runId)}`);
      const data = await res.json();
      if (!res.ok) {
        setStatus(data?.error ? String(data.error) : "Falha ao gerar link.");
        return;
      }
      window.open(String(data.url), "_blank", "noopener,noreferrer");
      setStatus("Download iniciado.");
    } catch (e: any) {
      setStatus(String(e));
    }
  }

  function editRun(runId: string) {
    const ok = window.confirm("Deseja mesmo editar esta cotação? Isso abrirá a prévia automaticamente.");
    if (!ok) return;
    window.location.href = `/precos?edit_run_id=${encodeURIComponent(runId)}`;
  }

  return (
    <main style={{ margin: "12px 0 0", padding: "0 0 110px" }}>
      <h1 style={{ marginBottom: 6 }}>Arquivamentos</h1>

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <input
          value={filtroLista}
          onChange={(e) => setFiltroLista(e.target.value)}
          placeholder="Filtrar por Nº da lista..."
          style={{
            padding: "6px 8px",
            width: 220,
            border: "1px solid #cfcfcf",
            borderRadius: 6,
          }}
        />
        <button onClick={load} disabled={loading} style={{ padding: "8px 10px" }}>
          {loading ? "Carregando..." : "Atualizar"}
        </button>
      </div>

      {status && <p style={{ marginTop: 10 }}>{status}</p>}

      <div style={{ marginTop: 12, overflowX: "auto" }}>
        <table
          style={{
            borderCollapse: "collapse",
            width: "100%",
            tableLayout: "fixed",
            fontSize: 14,
          }}
        >
          <colgroup>
            <col style={{ width: "10%" }} />
            <col style={{ width: "22%" }} />
            <col style={{ width: "14%" }} />
            <col style={{ width: "14%" }} />
            <col style={{ width: "14%" }} />
            <col style={{ width: "14%" }} />
            <col style={{ width: "8%" }} />
            <col style={{ width: "4%" }} />
          </colgroup>

          <thead>
            <tr>
              {["Lista", "Nome da lista", "Responsável", "Processo SEI", "Salvo em", "Última edição em", "Tamanho", "Ações"].map((h) => (
                <th
                  key={h}
                  style={{
                    border: "1px solid #ddd",
                    padding: "8px 8px",
                    background: "#f7f7f7",
                    textAlign: "center",
                    whiteSpace: "normal",
                    fontWeight: 700,
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {rows.map((r, idx) => {
              const runId = r.latest_run_id || "";
              const baseBg = idx % 2 === 0 ? "#ffffff" : "#f4f4f4";
              return (
                <tr key={`${r.numero_lista}-${idx}`} style={{ background: baseBg }}>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>{r.numero_lista}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>{r.nome_lista || ""}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>{r.responsavel || ""}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>{r.processo_sei || ""}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>{fmtDate(r.salvo_em)}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>{fmtDate(r.ultima_edicao_em)}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>{fmtBytes(r.tamanho_bytes)}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>
                    <div style={{ display: "inline-flex", gap: 8 }}>
                      <button
                        title="Baixar .zip"
                        onClick={() => runId && presignAndDownload(runId)}
                        disabled={!runId}
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          justifyContent: "center",
                          padding: "6px 8px",
                          border: "1px solid #cfcfcf",
                          borderRadius: 6,
                          background: "#fff",
                          cursor: runId ? "pointer" : "not-allowed",
                        }}
                      >
                        <IconDownload />
                      </button>

                      <button
                        title="Editar cotação"
                        onClick={() => runId && editRun(runId)}
                        disabled={!runId}
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          justifyContent: "center",
                          padding: "6px 8px",
                          border: "1px solid #cfcfcf",
                          borderRadius: 6,
                          background: "#fff",
                          cursor: runId ? "pointer" : "not-allowed",
                        }}
                      >
                        <IconPencil />
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}

            {!rows.length && (
              <tr>
                <td colSpan={8} style={{ border: "1px solid #ddd", padding: "10px 8px", textAlign: "center" }}>
                  Nenhum arquivamento encontrado.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </main>
  );
}
