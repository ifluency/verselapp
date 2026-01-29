import React from "react";

/**
 * DEPRECADO: o projeto agora usa Auth.js (NextAuth v5) + middleware.
 * Mantido apenas para compatibilidade caso exista algum import legado.
 */
export default function AuthGuard({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
