"use client";

import React, { useEffect, useMemo, useState } from "react";

type ArchiveRunRow = {
  numero_lista: string;
  nome_lista: string;
  processo_sei: string;
  responsavel_atual: string;
  run_id: string;
  run_number: number;
  saved_at_iso: string;
  r2_key: string;
  sha256_zip: string;
  size_bytes: number | null;
};

function fmtDateTimeBR(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString("pt-BR");
  } catch {
    return iso;
  }
}

function fmtBytes(n: number | null | undefined): string {
  if (!n || !Number.isFinite(n)) return "";
  const units = ["B", "KB", "MB", "GB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  const dec = i === 0 ? 0 : 2;
  return `${v.toFixed(dec).replace(".", ",")} ${units[i]}`;
}

export default function ArquivamentosPage() {
  const [numeroLista, setNumeroLista] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string>("");
  const [rows, setRows] = useState<ArchiveRunRow[]>([]);

  const [presignLoadingId, setPresignLoadingId] = useState<string>("");
  const [toast, setToast] = useState<string>("");

  const hasDbHint = useMemo(() => true, []);

  async function loadRuns() {
    setLoading(true);
    setError("");
    setRows([]);
    try {
      const qs = new URLSearchParams();
      if (numeroLista.trim()) qs.set("numero_lista", numeroLista.trim());
      qs.set("limit", "100");
      const res = await fetch(`/api/archive_runs?${qs.toString()}`);
      const txt = await res.text();
      if (!res.ok) {
        throw new Error(txt || `HTTP ${res.status}`);
      }
      const data = JSON.parse(txt);
      setRows((data.rows || []) as ArchiveRunRow[]);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }

  async function copyPresigned(runId: string) {
    setPresignLoadingId(runId);
    setToast("");
    try {
      const res = await fetch(`/api/archive_presign?run_id=${encodeURIComponent(runId)}`);
      const txt = await res.text();
      if (!res.ok) throw new Error(txt || `HTTP ${res.status}`);
      const data = JSON.parse(txt);
      const url = String(data.url || "");
      if (!url) throw new Error("URL não retornada.");
      await navigator.clipboard.writeText(url);
      setToast("Link copiado!");
      setTimeout(() => setToast(""), 2500);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setPresignLoadingId("");
    }
  }

  async function openDownload(runId: string) {
    setPresignLoadingId(runId);
    setToast("");
    try {
      const res = await fetch(`/api/archive_presign?run_id=${encodeURIComponent(runId)}`);
      const txt = await res.text();
      if (!res.ok) throw new Error(txt || `HTTP ${res.status}`);
      const data = JSON.parse(txt);
      const url = String(data.url || "");
      if (!url) throw new Error("URL não retornada.");
      window.open(url, "_blank", "noopener,noreferrer");
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setPresignLoadingId("");
    }
  }

  useEffect(() => {
    // Carrega automaticamente ao abrir a aba.
    loadRuns();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const th: React.CSSProperties = {
    border: "1px solid #e5e7eb",
    padding: "8px 8px",
    fontSize: 12,
    textAlign: "left",
    background: "#f9fafb",
    position: "sticky",
    top: 0,
    zIndex: 1,
  };

  const td: React.CSSProperties = {
    border: "1px solid #e5e7eb",
    padding: "8px 8px",
    fontSize: 12,
    verticalAlign: "top",
  };

  return (
    <main style={{ margin: "12px 0 0", padding: "0 0 110px" }}>
      <div style={{ display: "grid", gap: 10 }}>
        <div style={{ display: "grid", gap: 4 }}>
          <div style={{ fontSize: 18, fontWeight: 900, color: "#111827" }}>Histórico de Arquivamentos (R2)</div>
          <div style={{ fontSize: 12, color: "#6b7280" }}>
            Consulte as versões arquivadas de cada Lista (runs). Para funcionar, é necessário que o servidor tenha
            <b> DATABASE_URL</b> configurada.
          </div>
        </div>

        <div
          style={{
            display: "flex",
            gap: 10,
            flexWrap: "wrap",
            alignItems: "flex-end",
            padding: 12,
            border: "1px solid #e5e7eb",
            borderRadius: 12,
            background: "#ffffff",
          }}
        >
          <div style={{ display: "grid", gap: 6 }}>
            <label style={{ fontSize: 12, fontWeight: 800, color: "#374151" }}>Número da Lista (opcional)</label>
            <input
              value={numeroLista}
              onChange={(e) => setNumeroLista(e.target.value)}
              placeholder="Ex.: 001-2026"
              style={{
                height: 36,
                width: 220,
                border: "1px solid #d1d5db",
                borderRadius: 10,
                padding: "0 10px",
                fontSize: 13,
              }}
            />
          </div>
          <button
            type="button"
            className="btn"
            onClick={loadRuns}
            disabled={loading}
            style={{ height: 36, borderRadius: 10, padding: "0 12px" }}
          >
            {loading ? "Carregando..." : "Atualizar"}
          </button>

          {toast && (
            <div
              style={{
                marginLeft: "auto",
                padding: "8px 10px",
                borderRadius: 10,
                border: "1px solid #bbf7d0",
                background: "#f0fdf4",
                color: "#166534",
                fontSize: 12,
                fontWeight: 800,
              }}
            >
              {toast}
            </div>
          )}
        </div>

        {!!error && (
          <div style={{ padding: 10, borderRadius: 12, background: "#fef2f2", border: "1px solid #fecaca", color: "#991b1b", fontSize: 12 }}>
            {error}
          </div>
        )}

        <div style={{ overflowX: "auto", border: "1px solid #e5e7eb", borderRadius: 12, background: "#ffffff" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 1050 }}>
            <thead>
              <tr>
                <th style={th}>Lista</th>
                <th style={th}>Responsável</th>
                <th style={th}>Processo SEI</th>
                <th style={th}>Run</th>
                <th style={th}>Salvo em</th>
                <th style={th}>Tamanho</th>
                <th style={th}>SHA-256</th>
                <th style={th}>R2 Key</th>
                <th style={th}>Ações</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, idx) => {
                const zebra = idx % 2 === 0 ? "#ffffff" : "#f9fafb";
                const busy = presignLoadingId === r.run_id;
                return (
                  <tr key={r.run_id} style={{ background: zebra }}>
                    <td style={td}>
                      <div style={{ fontWeight: 900, color: "#111827" }}>{r.numero_lista}</div>
                      {!!r.nome_lista && <div style={{ color: "#6b7280" }}>{r.nome_lista}</div>}
                    </td>
                    <td style={td}>{r.responsavel_atual}</td>
                    <td style={td}>{r.processo_sei}</td>
                    <td style={td}>
                      <div style={{ fontWeight: 900 }}>#{r.run_number}</div>
                      <div style={{ fontSize: 11, color: "#6b7280" }}>{r.run_id}</div>
                    </td>
                    <td style={td}>{fmtDateTimeBR(r.saved_at_iso)}</td>
                    <td style={td}>{fmtBytes(r.size_bytes)}</td>
                    <td style={td}>
                      <div style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace" }}>
                        {r.sha256_zip}
                      </div>
                    </td>
                    <td style={td}>
                      <div
                        title={r.r2_key}
                        style={{
                          maxWidth: 260,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
                        }}
                      >
                        {r.r2_key}
                      </div>
                    </td>
                    <td style={td}>
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <button
                          type="button"
                          className="btn"
                          disabled={busy}
                          onClick={() => copyPresigned(r.run_id)}
                          style={{ height: 32, borderRadius: 10, padding: "0 10px" }}
                          title="Gera um link temporário (presigned) e copia"
                        >
                          {busy ? "Gerando..." : "Copiar link"}
                        </button>
                        <button
                          type="button"
                          className="btn btnGhost"
                          disabled={busy}
                          onClick={() => openDownload(r.run_id)}
                          style={{ height: 32, borderRadius: 10, padding: "0 10px" }}
                          title="Gera link temporário (presigned) e abre em nova aba"
                        >
                          Abrir
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}

              {!loading && rows.length === 0 && (
                <tr>
                  <td style={td} colSpan={9}>
                    <div style={{ color: "#6b7280" }}>
                      Nenhum arquivamento encontrado.
                      {hasDbHint ? "" : ""}
                    </div>
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
