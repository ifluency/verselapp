// app/login-actions.ts
"use server";

import { signIn } from "../auth";
import { redirect } from "next/navigation";

/**
 * Login normal (form)
 */
export async function doLogin(formData: FormData) {
  const login = String(formData.get("login") || "").trim();
  const password = String(formData.get("password") || "");

  try {
    await signIn("credentials", { login, password, redirectTo: "/precos" });
  } catch (e: any) {
    console.error("LOGIN_FAILED", { message: e?.message, cause: e?.cause });
    redirect("/?error=1");
  }
}

/**
 * Login rápido (sem digitar), mas com autenticação REAL.
 * - Não expõe senha no client.
 * - Só habilita se ALLOW_QUICK_LOGIN=true e NODE_ENV != 'production'
 */
export async function doQuickLogin() {
  if (process.env.NODE_ENV === "production") {
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
    console.error("QUICK_LOGIN_FAILED", { message: e?.message, cause: e?.cause });
    redirect("/?error=1");
  }
}
