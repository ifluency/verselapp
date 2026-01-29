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
  r2_key_archive: string | null;
  r2_key_input_pdf: string | null;
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

async function readJsonSafe(res: Response) {
  const ct = res.headers.get("content-type") || "";
  const text = await res.text();

  // Try JSON first (even if content-type is wrong)
  try {
    return { ok: true as const, data: JSON.parse(text) as any, raw: text, contentType: ct };
  } catch {
    const snippet = text.slice(0, 260).replace(/\s+/g, " ").trim();
    return {
      ok: false as const,
      error: `Endpoint ${res.url} não retornou JSON. HTTP ${res.status} ${res.statusText}. content-type="${ct}". Início da resposta: ${snippet}`,
      raw: text,
      contentType: ct,
    };
  }
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

function IconTrash() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3 6h18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M8 6V4h8v2" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
      <path d="M6 6l1 16h10l1-16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
      <path d="M10 11v6M14 11v6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
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
      const qs = filtroLista ? `&lista=${encodeURIComponent(filtroLista)}` : "";
      const res = await fetch(`/api/archive?action=runs${qs}`);
      const parsed = await readJsonSafe(res);
      if (!parsed.ok) {
        setStatus(parsed.error);
        return;
      }
      const data = parsed.data;
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
      const parsed = await readJsonSafe(res);
      if (!parsed.ok) {
        setStatus(parsed.error);
        return;
      }
      const data = parsed.data;
      if (!res.ok) {
        setStatus(data?.error ? String(data.error) : "Falha ao presign.");
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
    const ok = window.confirm("Deseja mesmo APAGAR este arquivamento? Esta ação não pode ser desfeita.");
    if (!ok) return;

    setStatus("Apagando arquivamento...");
    try {
      const res = await fetch(`/api/archive?action=delete&run_id=${encodeURIComponent(runId)}`, { method: "POST" });
      const parsed = await readJsonSafe(res);
      if (!parsed.ok) {
        setStatus(parsed.error);
        return;
      }
      const data = parsed.data;
      if (!res.ok) {
        setStatus(data?.error ? String(data.error) : "Falha ao apagar.");
        return;
      }
      setStatus("Arquivamento apagado.");
      await load();
    } catch (e: any) {
      setStatus(String(e));
    }
  }

  return (
    <main style={{ maxWidth: "100%", margin: "12px auto", padding: "0 12px" }}>
      <h1 style={{ marginBottom: 8 }}>Arquivamentos</h1>

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <input
          value={filtroLista}
          onChange={(e) => setFiltroLista(e.target.value)}
          placeholder="Filtrar por Nº da lista..."
          style={{ padding: "6px 8px", width: 220 }}
        />
        <button onClick={load} disabled={loading}>
          {loading ? "Carregando..." : "Atualizar"}
        </button>
      </div>

      {status && <p style={{ marginTop: 10, whiteSpace: "pre-wrap" }}>{status}</p>}

      <div style={{ marginTop: 12, overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed", fontSize: 14 }}>
          <colgroup>
            <col style={{ width: "10%" }} />
            <col style={{ width: "20%" }} />
            <col style={{ width: "14%" }} />
            <col style={{ width: "14%" }} />
            <col style={{ width: "14%" }} />
            <col style={{ width: "14%" }} />
            <col style={{ width: "8%" }} />
            <col style={{ width: "6%" }} />
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
                    textAlign: "left",
                    fontWeight: 800,
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
              const hasArchive = Boolean((r.r2_key_archive || "").trim());
              const canEdit = hasArchive || Boolean((r.r2_key_input_pdf || "").trim());

              return (
                <tr key={`${r.numero_lista}-${idx}`} style={{ background: idx % 2 === 0 ? "#fff" : "#f4f4f4" }}>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>{r.numero_lista}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>{r.nome_lista || ""}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>{r.responsavel || ""}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>{r.processo_sei || ""}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>{fmtDate(r.salvo_em)}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>{fmtDate(r.ultima_edicao_em)}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>{fmtBytes(r.tamanho_bytes)}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <button
                        title={hasArchive ? "Baixar .zip" : "Sem arquivo arquivado no R2"}
                        onClick={() => runId && presignAndDownload(runId)}
                        disabled={!runId || !hasArchive}
                        style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", padding: "6px 8px" }}
                      >
                        <IconDownload />
                      </button>

                      <button
                        title={canEdit ? "Editar cotação" : "Sem arquivos no R2 para reabrir"}
                        onClick={() => runId && editRun(runId)}
                        disabled={!runId || !canEdit}
                        style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", padding: "6px 8px" }}
                      >
                        <IconPencil />
                      </button>

                      <button
                        title="Apagar arquivamento"
                        onClick={() => runId && deleteRun(runId)}
                        disabled={!runId}
                        style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", padding: "6px 8px" }}
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
                <td colSpan={8} style={{ border: "1px solid #ddd", padding: "12px 8px" }}>
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
