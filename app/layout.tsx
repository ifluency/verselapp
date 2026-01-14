export const metadata = {
  title: "Extrator de Cotação (PDF → Excel)",
  description: "Upload de PDF e geração de Excel (Compõe = Sim).",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="pt-BR">
      <body style={{ margin: 0 }}>{children}</body>
    </html>
  );
}
