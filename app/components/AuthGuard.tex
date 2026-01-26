"use client";

import React, { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";

export const AUTH_KEY = "upde_auth_v1";

function isAuthed(): boolean {
  try {
    return window.localStorage.getItem(AUTH_KEY) === "1";
  } catch {
    return false;
  }
}

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // No App Router, layouts server-side não conseguem acessar localStorage.
    // Proteção client-side: se não estiver autenticado, redireciona para /.
    if (!isAuthed()) {
      // Evita loop caso algo esteja errado.
      if (pathname !== "/") router.replace("/");
      return;
    }
    setReady(true);
  }, [router, pathname]);

  if (!ready) {
    return (
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "24px 16px" }}>
        <div style={{ color: "#4b5563", fontSize: 13, fontWeight: 700 }}>Verificando acesso...</div>
      </div>
    );
  }

  return <>{children}</>;
}
