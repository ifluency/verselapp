"use server";

import { signIn } from "../auth";
import { redirect } from "next/navigation";

/**
 * Server Action do login (Credentials).
 * Em caso de erro, redireciona para "/?error=1".
 */
export async function doLogin(formData: FormData) {
  const email = String(formData.get("email") || "").trim();
  const password = String(formData.get("password") || "");

  try {
    await signIn("credentials", { email, password, redirectTo: "/precos" });
  } catch (err) {
    // Em Server Actions, o Auth.js pode lançar erro ao invés de redirecionar.
    // Mantemos UX simples via query param.
    redirect("/?error=1");
  }
}
