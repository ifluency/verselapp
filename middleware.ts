export { auth as middleware } from "./auth";

// Protege tudo exceto api, assets e a página de login ("/")
export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
