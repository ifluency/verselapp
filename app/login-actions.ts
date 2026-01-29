// app/login-actions.ts
"use server";

import { signIn } from "../auth";
import { redirect } from "next/navigation";

/**
 * Server Action do login (Credentials).
 * Em caso de erro, redireciona para "/?error=1".
 */
export async function doLogin(formData: FormData) {
  const login = String(formData.get("login") || "").trim();
  const password = String(formData.get("password") || "");

  try {
    await signIn("credentials", { login, password, redirectTo: "/precos" });
  } catch {
    redirect("/?error=1");
  }
}
