"use server";

import { signIn } from "../auth";
import { redirect } from "next/navigation";

export async function doLogin(formData: FormData) {
  const login = String(formData.get("login") || "").trim();
  const password = String(formData.get("password") || "");

  try {
    await signIn("credentials", { login, password, redirectTo: "/precos" });
  } catch (e: any) {
    // Isso vai aparecer em Logs -> Functions na Vercel
    console.error("LOGIN_FAILED", {
      message: e?.message,
      name: e?.name,
      cause: e?.cause,
      stack: e?.stack,
    });

    redirect("/?error=1");
  }
}
