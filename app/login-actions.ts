// app/login-actions.ts
"use server";

import { signIn } from "../auth";
import { redirect } from "next/navigation";

function isNextRedirectError(e: any) {
  return (
    e?.message === "NEXT_REDIRECT" ||
    (typeof e?.digest === "string" && e.digest.startsWith("NEXT_REDIRECT"))
  );
}

/**
 * Login normal (form)
 */
export async function doLogin(formData: FormData) {
  const login = String(formData.get("login") || "").trim();
  const password = String(formData.get("password") || "");

  try {
    await signIn("credentials", { login, password, redirectTo: "/precos" });
  } catch (e: any) {
    // Se for redirect do Next, não tratar como erro
    if (isNextRedirectError(e)) throw e;

    console.error("LOGIN_FAILED", {
      message: e?.message,
      name: e?.name,
      cause: e?.cause,
    });

    redirect("/?error=1");
  }
}

/**
 * Login rápido (sem digitar), mas com autenticação REAL.
 * - Não expõe senha no client.
 * - Só habilita se ALLOW_QUICK_LOGIN=true e VERCEL_ENV != 'production'
 */
export async function doQuickLogin() {
  const vercelEnv = (process.env.VERCEL_ENV || "").toLowerCase();
  if (vercelEnv === "production") {
    redirect("/?error=quick_login_disabled");
  }
  if ((process.env.ALLOW_QUICK_LOGIN || "").toLowerCase() !== "true") {
    redirect("/?error=quick_login_disabled");
  }

  const login = String(process.env.ADMIN_USERNAME || process.env.ADMIN_EMAIL || "").trim();
  const password = String(process.env.ADMIN_PASSWORD || "");

  if (!login || !password) {
    redirect("/?error=missing_admin_env");
  }

  try {
    await signIn("credentials", { login, password, redirectTo: "/precos" });
  } catch (e: any) {
    if (isNextRedirectError(e)) throw e;

    console.error("QUICK_LOGIN_FAILED", {
      message: e?.message,
      name: e?.name,
      cause: e?.cause,
    });

    redirect("/?error=1");
  }
}
