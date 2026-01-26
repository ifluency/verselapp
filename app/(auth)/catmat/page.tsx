"use client";

import React, { useMemo, useState } from "react";

type CatmatItem = {
  codigo: string;
  descricao: string;
  unidade: string;
};

// MVP: dados mockados para validar UX. Integração com fonte oficial/API pode ser feita depois.
const MOCK: CatmatItem[] = [
  { codigo: "000000001", descricao: "Soro fisiológico 0,9% 1000 mL", unidade: "Bolsa" },
  { codigo: "000000002", descricao: "Seringa descartável 10 mL", unidade: "Unidade" },
  { codigo: "000000003", descricao: "Luva cirúrgica estéril", unidade: "Par" },
  { codigo: "000000004", descricao: "Equipo macro gotas", unidade: "Unidade" },
];

export default function CatmatPage() {
  const [q, setQ] = useState("");

  const results = useMemo(() => {
    const query = q.trim().toLowerCase();
    if (!query) return MOCK;
    return MOCK.filter((i) => {
      return i.codigo.toLowerCase().includes(query) || i.descricao.toLowerCase().includes(query);
    });
  }, [q]);

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
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ display: "grid", gap: 6, flex: 1, minWidth: 260 }}>
            <label style={{ fontWeight: 800, fontSize: 13 }}>Buscar CATMAT</label>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Digite o código ou parte da descrição"
              style={{
                height: 40,
                borderRadius: 10,
                border: "1px solid #cbd5e1",
                padding: "0 12px",
                fontSize: 14,
              }}
            />
          </div>

          <button
            className="btn btnPrimary"
            onClick={() => setQ("")}
            disabled={!q.trim()}
            title="Limpar busca"
            style={{ height: 40 }}
          >
            Limpar
          </button>
        </div>

        <div style={{ marginTop: 10, color: "#6b7280", fontSize: 12 }}>
          MVP de consulta CATMAT (mock). Integração com a base oficial pode ser adicionada na próxima etapa.
        </div>
      </div>

      <div style={{ marginTop: 12, overflowX: "auto" }}>
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            border: "1px solid #e5e7eb",
            borderRadius: 12,
            overflow: "hidden",
          }}
        >
          <thead>
            <tr style={{ background: "#f9fafb" }}>
              <th style={th}>Código</th>
              <th style={th}>Descrição</th>
              <th style={th}>Unidade</th>
            </tr>
          </thead>
          <tbody>
            {results.map((r) => (
              <tr key={r.codigo}>
                <td style={tdMono}>{r.codigo}</td>
                <td style={td}>{r.descricao}</td>
                <td style={td}>{r.unidade}</td>
              </tr>
            ))}
            {!results.length && (
              <tr>
                <td style={td} colSpan={3}>
                  Nenhum resultado encontrado.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </main>
  );
}

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
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
  fontSize: 12,
};
