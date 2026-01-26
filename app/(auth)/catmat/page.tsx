"use client";

import React, { useMemo, useState } from "react";

type CatmatQueryRow = {
  seq: number;
  catmat: string;
};

type CatmatResultRow = {
  seq: number;
  catmat: string;
  descritivo: string;
  statusItem: boolean | null;
  error?: string;
};

type ApiResponse = {
  results: {
    codigoItem: string;
    ok: boolean;
    statusItem: boolean | null;
    descricaoItem: string;
    error?: string;
  }[];
};

function normalizeCatmatToken(token: string): string {
  // Pega somente dígitos (CATMAT costuma ser numérico). Mantém zeros à esquerda.
  const digits = (token || "").replace(/\D+/g, "");
  return digits;
}

function parseCatmatList(raw: string): CatmatQueryRow[] {
  const lines = (raw || "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .split("\n");

  const out: CatmatQueryRow[] = [];

  for (const line of lines) {
    // Excel pode vir com TAB/; etc. Pegamos a 1ª célula.
    const firstCell = (line || "").split("\t")[0].trim();
    if (!firstCell) continue;

    // Possível cabeçalho: "catmat" (qualquer case)
    if (firstCell.trim().toLowerCase() === "catmat") continue;

    const cat = normalizeCatmatToken(firstCell);
    if (!cat) continue;

    out.push({ seq: out.length + 1, catmat: cat });
  }

  return out;
}

export default function CatmatPage() {
  const [rawInput, setRawInput] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<CatmatResultRow[]>([]);

  const parsed = useMemo(() => parseCatmatList(rawInput), [rawInput]);

  const ativos = useMemo(
    () => results.filter((r) => r.statusItem === true).sort((a, b) => a.seq - b.seq),
    [results]
  );
  const inativos = useMemo(
    () => results.filter((r) => r.statusItem === false).sort((a, b) => a.seq - b.seq),
    [results]
  );
  const semRetorno = useMemo(
    () => results.filter((r) => r.statusItem === null).sort((a, b) => a.seq - b.seq),
    [results]
  );

  async function consultar() {
    const rows = parsed;
    if (!rows.length) {
      setStatus("Cole uma lista de CATMATs (1 por linha) para consultar.");
      setResults([]);
      return;
    }

    setLoading(true);
    setStatus("Consultando CATMATs na API oficial...");
    setResults([]);

    try {
      const res = await fetch("/api/catmat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ codes: rows.map((r) => r.catmat) }),
      });

      if (!res.ok) {
        const msg = await res.text();
        setStatus(`Falha ao consultar CATMATs: ${msg}`);
        return;
      }

      const data = (await res.json()) as ApiResponse;
      const map = new Map<string, (typeof data.results)[number]>();
      for (const r of data.results || []) map.set(String(r.codigoItem), r);

      const merged: CatmatResultRow[] = rows.map((r) => {
        const api = map.get(String(r.catmat));
        if (!api) {
          return {
            seq: r.seq,
            catmat: r.catmat,
            descritivo: "",
            statusItem: null,
            error: "Sem retorno",
          };
        }
        return {
          seq: r.seq,
          catmat: r.catmat,
          descritivo: api.descricaoItem || "",
          statusItem: api.statusItem,
          error: api.ok ? undefined : api.error || "Erro",
        };
      });

      setResults(merged);

      const total = rows.length;
      const a = merged.filter((x) => x.statusItem === true).length;
      const i = merged.filter((x) => x.statusItem === false).length;
      const s = merged.filter((x) => x.statusItem === null).length;

      setStatus(
        s
          ? `Concluído. Pesquisados: ${total}. Ativos: ${a}. Inativos: ${i}. Sem retorno/erro: ${s}.`
          : `Concluído. Pesquisados: ${total}. Ativos: ${a}. Inativos: ${i}.`
      );
    } catch (e: any) {
      setStatus(`Falha ao consultar CATMATs: ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }

  function limparTudo() {
    setRawInput("");
    setResults([]);
    setStatus("");
  }

  return (
    <main style={{ margin: "12px 0 0", padding: "0 0 24px" }}>
      <div
        style={{
          marginTop: 12,
          border: "1px solid #e5e7eb",
          borderRadius: 12,
          padding: 14,
          background: "#ffffff",
        }}
      >
        <div style={{ display: "grid", gap: 10 }}>
          <div style={{ display: "grid", gap: 6 }}>
            <label style={{ fontWeight: 900, fontSize: 13 }}>
              Cole a lista de CATMATs (1 por linha)
            </label>
            <textarea
              value={rawInput}
              onChange={(e) => setRawInput(e.target.value)}
              placeholder={"Exemplo:\ncatmat\n123456\n789012\n..."}
              style={{
                width: "100%",
                minHeight: 160,
                borderRadius: 12,
                border: "1px solid #cbd5e1",
                padding: 12,
                fontSize: 13,
                fontFamily:
                  "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
              }}
              disabled={loading}
            />
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <button
                className="btn btnPrimary"
                onClick={consultar}
                disabled={loading || !parsed.length}
                style={{ height: 40 }}
                title={
                  !parsed.length
                    ? "Cole uma lista de CATMATs para habilitar a consulta"
                    : "Consultar na API de cadastro de materiais"
                }
              >
                {loading ? "Consultando..." : "Consultar"}
              </button>

              <button
                className="btn"
                onClick={limparTudo}
                disabled={loading && !rawInput}
                style={{ height: 40 }}
                title="Limpar campo e resultados"
              >
                Limpar
              </button>

              <div style={{ fontSize: 12, color: "#6b7280" }}>
                Linhas válidas detectadas: <strong>{parsed.length}</strong>
              </div>
            </div>
            <div style={{ fontSize: 12, color: "#6b7280" }}>
              A aplicação ignora cabeçalho “catmat” e linhas em branco, e tolera colagem vinda do Excel (com TAB).
            </div>
          </div>

          {status && (
            <div
              style={{
                padding: "10px 12px",
                borderRadius: 12,
                background: "#f9fafb",
                border: "1px solid #e5e7eb",
                fontSize: 13,
              }}
            >
              {status}
            </div>
          )}
        </div>
      </div>

      {/* Resultados */}
      {!!results.length && (
        <>
          <SectionTable title="CATMATs Ativos" rows={ativos} emptyText="Nenhum CATMAT ativo encontrado." />
          <SectionTable
            title="CATMATs Inativos"
            rows={inativos}
            emptyText="Nenhum CATMAT inativo encontrado."
          />

          {/* Sem retorno/erro (não é tabela, para manter o requisito de 2 tabelas) */}
          {!!semRetorno.length && (
            <div
              style={{
                marginTop: 14,
                border: "1px solid #fde68a",
                background: "#fffbeb",
                borderRadius: 12,
                padding: 12,
              }}
            >
              <div style={{ fontWeight: 900, marginBottom: 6 }}>Sem retorno/erro na consulta</div>
              <div style={{ fontSize: 13, color: "#374151" }}>
                {semRetorno.map((r) => (
                  <div key={r.seq} style={{ marginTop: 4 }}>
                    <strong>{r.seq}.</strong> CATMAT <span style={{ fontFamily: mono }}>{r.catmat}</span>
                    {r.error ? <span style={{ opacity: 0.8 }}> — {r.error}</span> : null}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Resumo */}
          <div
            style={{
              marginTop: 14,
              border: "1px solid #e5e7eb",
              borderRadius: 12,
              padding: 12,
              background: "#ffffff",
            }}
          >
            <div style={{ fontWeight: 900, marginBottom: 6 }}>Resumo</div>
            <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 13 }}>
              <SummaryPill label="Pesquisados" value={parsed.length} />
              <SummaryPill label="Ativos" value={ativos.length} />
              <SummaryPill label="Inativos" value={inativos.length} />
              {!!semRetorno.length && <SummaryPill label="Sem retorno/erro" value={semRetorno.length} />}
            </div>
          </div>
        </>
      )}
    </main>
  );
}

function SectionTable({
  title,
  rows,
  emptyText,
}: {
  title: string;
  rows: CatmatResultRow[];
  emptyText: string;
}) {
  return (
    <div style={{ marginTop: 14, overflowX: "auto" }}>
      <div style={{ fontWeight: 900, margin: "0 0 8px", fontSize: 13 }}>{title}</div>
      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
          border: "1px solid #e5e7eb",
          borderRadius: 12,
          overflow: "hidden",
          background: "#ffffff",
        }}
      >
        <thead>
          <tr style={{ background: "#f9fafb" }}>
            <th style={th}>Sequência</th>
            <th style={th}>CATMAT</th>
            <th style={th}>Descritivo</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.seq}-${r.catmat}`}>
              <td style={tdMono}>{r.seq}</td>
              <td style={tdMono}>{r.catmat}</td>
              <td style={td}>{r.descritivo || ""}</td>
            </tr>
          ))}
          {!rows.length && (
            <tr>
              <td style={td} colSpan={3}>
                {emptyText}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function SummaryPill({ label, value }: { label: string; value: number }) {
  return (
    <div
      style={{
        border: "1px solid #e5e7eb",
        background: "#f9fafb",
        borderRadius: 999,
        padding: "6px 10px",
      }}
    >
      <span style={{ fontWeight: 800 }}>{label}:</span> <span>{value}</span>
    </div>
  );
}

const mono =
  "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace";

const th: React.CSSProperties = {
  textAlign: "left",
  padding: "10px 12px",
  fontSize: 12,
  fontWeight: 900,
  color: "#111827",
  borderBottom: "1px solid #e5e7eb",
};

const td: React.CSSProperties = {
  padding: "10px 12px",
  fontSize: 13,
  borderBottom: "1px solid #f3f4f6",
  color: "#111827",
  verticalAlign: "top",
};

const tdMono: React.CSSProperties = {
  ...td,
  fontFamily: mono,
  fontSize: 12,
};
