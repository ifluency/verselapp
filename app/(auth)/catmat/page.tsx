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
  // Mantém somente dígitos (CATMAT costuma ser numérico). Mantém zeros à esquerda.
  const digits = (token || "").replace(/\D+/g, "");
  return digits;
}

function parseCatmatList(raw: string): CatmatQueryRow[] {
  const lines = (raw || "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .split("\n");

  const out: CatmatQueryRow[] = [];
  let seq = 1;

  for (const line of lines) {
    // Excel pode vir com TAB. Pega a 1ª coluna.
    const firstCell = String(line || "").split("\t")[0] ?? "";
    const catmat = normalizeCatmatToken(firstCell);

    // Ignora cabeçalhos/linhas inválidas (sem dígitos) e linhas em branco.
    if (!catmat) continue;

    out.push({ seq, catmat });
    seq += 1;
  }

  // Remove duplicados mantendo a primeira ocorrência (preserva ordem)
  const seen = new Set<string>();
  return out.filter((r) => {
    if (seen.has(r.catmat)) return false;
    seen.add(r.catmat);
    return true;
  });
}

function truncate(s: string, max = 150): string {
  const t = (s || "").trim();
  if (!t) return "";
  if (t.length <= max) return t;
  return t.slice(0, max).trimEnd() + "…";
}

export default function Page() {
  const [rawInput, setRawInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<React.ReactNode>(null);
  const [results, setResults] = useState<CatmatResultRow[]>([]);
  const [copied, setCopied] = useState(false);

  const parsed = useMemo(() => parseCatmatList(rawInput), [rawInput]);

  const ativos = useMemo(
    () => results.filter((r) => r.statusItem === true).sort((a, b) => a.seq - b.seq),
    [results]
  );
  const inativos = useMemo(
    () => results.filter((r) => r.statusItem === false).sort((a, b) => a.seq - b.seq),
    [results]
  );

  const erroCount = useMemo(
    () => results.filter((r) => r.statusItem === null || !!r.error).length,
    [results]
  );

  function limparTudo() {
    setRawInput("");
    setResults([]);
    setStatus(null);
    setCopied(false);
  }

  async function copiarInativos() {
    const text = inativos.map((r) => r.catmat).join("\n");
    if (!text) return;

    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // fallback
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      ta.style.top = "-9999px";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      try {
        document.execCommand("copy");
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      } finally {
        document.body.removeChild(ta);
      }
    }
  }

  async function consultar() {
    const rows = parsed;
    if (!rows.length) {
      setStatus("Cole a lista de CATMATs (1 por linha) para consultar.");
      setResults([]);
      return;
    }

    setLoading(true);
    setStatus("Consultando CATMATs na API oficial...");
    setResults([]);
    setCopied(false);

    try {
      const res = await fetch("/api/catmat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ codes: rows.map((r) => r.catmat) }),
      });

      if (!res.ok) {
        let msg = "";
        try {
          const ct = res.headers.get("content-type") || "";
          if (ct.includes("application/json")) {
            const j = await res.json();
            msg = String((j && (j.error || j.message)) || "");
          } else {
            msg = (await res.text()) || "";
          }
        } catch {
          msg = "";
        }

        const tail = msg ? ` ${msg}` : "";
        setStatus(`Falha ao consultar CATMATs: HTTP ${res.status}.${tail}`);
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
      const s = merged.filter((x) => x.statusItem === null || !!x.error).length;

      if (s) {
        setStatus(
          <span>
            Concluído! | Pesquisados: {total} | Ativos: {a} | Inativos: {i}. |{" "}
            <strong style={{ color: "#b91c1c" }}>Sem retorno/erro: {s}.</strong>
          </span>
        );
      } else {
        setStatus(`Concluído! | Pesquisados: ${total} | Ativos: ${a} | Inativos: ${i}.`);
      }
    } catch (e: any) {
      setStatus(`Falha ao consultar CATMATs: ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main style={{ margin: "12px 0 0", padding: "0 0 24px" }}>
      <div style={{ marginTop: 4, marginBottom: 10 }}>
        <div style={{ fontSize: 18, fontWeight: 900, color: "#111827" }}>Consulta CATMAT</div>
        <div style={{ marginTop: 4, fontSize: 13, color: "#4b5563" }}>
          Validação de status (ativo/inativo) via API de cadastro de materiais
        </div>
      </div>

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
            <label style={{ fontWeight: 900, fontSize: 13 }}>Cole a lista de CATMATs aqui, 1 por linha</label>
            <textarea
              value={rawInput}
              onChange={(e) => setRawInput(e.target.value)}
              placeholder={"Exemplo:\n123456\n789012\n098765\n654321\n..."}
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
              A aplicação ignora qualquer cabeçalho e também qualquer linha em branco.
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

      {!!results.length && (
        <>
          <div
            style={{
              marginTop: 14,
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(380px, 1fr))",
              gap: 14,
              alignItems: "start",
            }}
          >
            <StatusTable
              title="CATMATs Ativos"
              variant="active"
              rows={ativos}
              emptyText="Nenhum CATMAT ativo encontrado."
            />

            <StatusTable
              title="CATMATs Inativos"
              variant="inactive"
              rows={inativos}
              emptyText="Nenhum CATMAT inativo encontrado."
              rightAction={
                <button
                  className="btn btnGhost"
                  onClick={copiarInativos}
                  disabled={!inativos.length}
                  style={{ height: 32 }}
                  title="Copiar CATMATs inativos"
                >
                  {copied ? "Copiado!" : "Copiar inativos"}
                </button>
              }
            />
          </div>

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
            </div>

            {!!erroCount && (
              <div style={{ marginTop: 10, fontSize: 13, color: "#b91c1c" }}>
                <strong>catmats com erro:</strong> {erroCount}
              </div>
            )}
          </div>
        </>
      )}
    </main>
  );
}

function StatusTable({
  title,
  variant,
  rows,
  emptyText,
  rightAction,
}: {
  title: string;
  variant: "active" | "inactive";
  rows: CatmatResultRow[];
  emptyText: string;
  rightAction?: React.ReactNode;
}) {
  const headerBg = variant === "active" ? "#15803d" : "#b91c1c";

  return (
    <div style={{ border: "1px solid #e5e7eb", borderRadius: 12, overflow: "hidden", background: "#ffffff" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
          padding: "10px 12px",
          background: headerBg,
          color: "#ffffff",
          fontWeight: 900,
          fontSize: 13,
        }}
      >
        <div>{title}</div>
        {rightAction ? <div style={{ display: "flex", gap: 8 }}>{rightAction}</div> : null}
      </div>

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
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
                <td style={td}>{truncate(r.descritivo, 150)}</td>
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
    </div>
  );
}

function SummaryPill({ label, value }: { label: string; value: number }) {
  return (
    <div
      style={{
        border: "1px solid #e5e7eb",
        borderRadius: 999,
        padding: "6px 10px",
        background: "#f9fafb",
      }}
    >
      <span style={{ color: "#6b7280" }}>{label}:</span> <strong>{value}</strong>
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
  fontSize: 12,
  borderBottom: "1px solid #f3f4f6",
  color: "#111827",
  verticalAlign: "top",
};

const tdMono: React.CSSProperties = {
  ...td,
  fontFamily: mono,
};
