// middleware.ts
export { auth as middleware } from "./auth";

// Protege páginas, mas NÃO intercepta arquivos estáticos (ex.: /header_logos.png)
export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};
