import "./globals.css";

export const metadata = {
  title: "ANÁLISE DE PREÇOS - UPDE",
  description: "Formação de preços de referência com base em pesquisa do ComprasGOV.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
