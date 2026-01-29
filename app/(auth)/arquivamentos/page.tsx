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
      <path
        d="M12 20h9"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
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

function IconTrash() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M3 6h18"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <path
        d="M8 6V4h8v2"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <path
        d="M19 6l-1 14H6L5 6"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <path
        d="M10 11v6M14 11v6"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

function IconRefresh() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M20 12a8 8 0 1 1-2.34-5.66"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <path
        d="M20 4v6h-6"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
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

  const inputStyle: React.CSSProperties = {
    fontSize: 14,
    padding: "6px 8px",
    border: "1px solid #cbd5e1",
    background: "#ffffff",
    borderRadius: 6,
    height: 34,
    outline: "none",
  };

  const iconBtnStyle: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    padding: "6px 8px",
    height: 34,
    borderRadius: 8,
    border: "1px solid #cbd5e1",
    background: "#ffffff",
    cursor: "pointer",
  };

  async function load() {
    setLoading(true);
    setStatus("");
    try {
      const qs = filtroLista ? `&lista=${encodeURIComponent(filtroLista)}` : "";
      const res = await fetch(`/api/archive?action=runs${qs}`);
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
      const res = await fetch(`/api/archive?action=presign&run_id=${encodeURIComponent(runId)}`);
      const data = await res.json();
      if (!res.ok) {
        setStatus(data?.error ? String(data.error) : "Falha ao gerar link de download.");
        return;
      }
      const url = data.url as string;
      window.open(url, "_blank", "noopener,noreferrer");
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

  async function deleteRun(runId: string) {
    const ok = window.confirm("Deseja mesmo remover este arquivamento? Isso apagará o run e os arquivos no R2.");
    if (!ok) return;

    setStatus("Removendo arquivamento...");
    try {
      const res = await fetch(`/api/archive?action=delete&run_id=${encodeURIComponent(runId)}`, {
        method: "POST",
      });

      const ct = res.headers.get("content-type") || "";
      const data = ct.includes("application/json") ? await res.json() : { error: await res.text() };

      if (!res.ok) {
        setStatus(data?.error ? String(data.error) : "Falha ao remover.");
        return;
      }

      setStatus("Arquivamento removido.");
      await load();
    } catch (e: any) {
      setStatus(String(e));
    }
  }

  return (
    <main
      style={{
        margin: "12px -11px 0", // mesmo padrão da página de preços (reduz margens laterais)
        padding: "0 0 110px",
      }}
    >
      <div style={{ padding: "0 12px" }}>
        <h1 style={{ margin: "0 0 10px", fontSize: 22, fontWeight: 900 }}>Arquivamentos</h1>

        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <input
            value={filtroLista}
            onChange={(e) => setFiltroLista(e.target.value)}
            placeholder="Filtrar por Nº da lista..."
            style={{ ...inputStyle, width: 260 }}
          />

          <button
            onClick={load}
            disabled={loading}
            title="Atualizar"
            aria-label="Atualizar"
            style={{
              ...iconBtnStyle,
              opacity: loading ? 0.7 : 1,
              cursor: loading ? "not-allowed" : "pointer",
            }}
          >
            <IconRefresh />
          </button>

          {loading && <span style={{ fontSize: 13, color: "#475569" }}>Carregando...</span>}
        </div>

        {status && (
          <p style={{ marginTop: 10, fontSize: 13, color: "#0f172a", whiteSpace: "pre-wrap" }}>{status}</p>
        )}

        <div style={{ marginTop: 12 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed", fontSize: 14 }}>
            <colgroup>
              <col style={{ width: 90 }} />
              <col />
              <col style={{ width: 160 }} />
              <col style={{ width: 150 }} />
              <col style={{ width: 185 }} />
              <col style={{ width: 185 }} />
              <col style={{ width: 120 }} />
              <col style={{ width: 160 }} />
            </colgroup>

            <thead>
              <tr>
                {["Lista", "Nome da lista", "Responsável", "Processo SEI", "Salvo em", "Última edição em", "Tamanho", "Ações"].map(
                  (h) => (
                    <th
                      key={h}
                      style={{
                        border: "1px solid #e5e7eb",
                        padding: "10px 8px",
                        background: "#f8fafc",
                        textAlign: "left",
                        fontWeight: 900,
                      }}
                    >
                      {h}
                    </th>
                  )
                )}
              </tr>
            </thead>

            <tbody>
              {rows.map((r, idx) => {
                const runId = r.latest_run_id || "";
                return (
                  <tr key={`${r.numero_lista}-${idx}`} style={{ background: idx % 2 === 0 ? "#fff" : "#f9fafb" }}>
                    <td style={{ border: "1px solid #e5e7eb", padding: "10px 8px", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {r.numero_lista}
                    </td>

                    <td style={{ border: "1px solid #e5e7eb", padding: "10px 8px", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {r.nome_lista || ""}
                    </td>

                    <td style={{ border: "1px solid #e5e7eb", padding: "10px 8px", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {r.responsavel || ""}
                    </td>

                    <td style={{ border: "1px solid #e5e7eb", padding: "10px 8px", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {r.processo_sei || ""}
                    </td>

                    <td style={{ border: "1px solid #e5e7eb", padding: "10px 8px" }}>{fmtDate(r.salvo_em)}</td>

                    <td style={{ border: "1px solid #e5e7eb", padding: "10px 8px" }}>{fmtDate(r.ultima_edicao_em)}</td>

                    <td style={{ border: "1px solid #e5e7eb", padding: "10px 8px" }}>{fmtBytes(r.tamanho_bytes)}</td>

                    <td style={{ border: "1px solid #e5e7eb", padding: "8px 8px" }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "center" }}>
                        <button
                          title="Baixar .zip"
                          aria-label="Baixar .zip"
                          onClick={() => runId && presignAndDownload(runId)}
                          disabled={!runId}
                          style={{
                            ...iconBtnStyle,
                            opacity: runId ? 1 : 0.45,
                            cursor: runId ? "pointer" : "not-allowed",
                          }}
                        >
                          <IconDownload />
                        </button>

                        <button
                          title="Editar cotação"
                          aria-label="Editar cotação"
                          onClick={() => runId && editRun(runId)}
                          disabled={!runId}
                          style={{
                            ...iconBtnStyle,
                            opacity: runId ? 1 : 0.45,
                            cursor: runId ? "pointer" : "not-allowed",
                          }}
                        >
                          <IconPencil />
                        </button>

                        <button
                          title="Remover arquivamento"
                          aria-label="Remover arquivamento"
                          onClick={() => runId && deleteRun(runId)}
                          disabled={!runId}
                          style={{
                            ...iconBtnStyle,
                            opacity: runId ? 1 : 0.45,
                            cursor: runId ? "pointer" : "not-allowed",
                          }}
                        >
                          <IconTrash />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}

              {!rows.length && (
                <tr>
                  <td colSpan={8} style={{ border: "1px solid #e5e7eb", padding: "12px 8px" }}>
                    Nenhum arquivamento encontrado.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </main>
  );
}
