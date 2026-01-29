// auth.ts
import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import { neon } from "@neondatabase/serverless";
import bcrypt from "bcryptjs";
import crypto from "crypto";

type DbUser = {
  id: string;
  email: string;
  username: string | null;
  name: string | null;
  role: string;
  password_hash: string;
};

const sql = neon(process.env.DATABASE_URL || "");

function asString(v: unknown): string {
  return typeof v === "string" ? v : "";
}

async function ensureUsersSchema() {
  if (!process.env.DATABASE_URL) throw new Error("DATABASE_URL não configurada.");

  // Mantém o schema antigo (email) e adiciona username para login por "nome"
  await sql/* sql */ `
    CREATE TABLE IF NOT EXISTS app_users (
      id text PRIMARY KEY,
      email text UNIQUE NOT NULL,
      name text,
      role text NOT NULL DEFAULT 'user',
      password_hash text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      last_login_at timestamptz
    )
  `;

  // Migração leve: username
  await sql/* sql */ `ALTER TABLE app_users ADD COLUMN IF NOT EXISTS username text`;
  await sql/* sql */ `UPDATE app_users SET username = email WHERE username IS NULL`;

  // Índices
  await sql/* sql */ `CREATE INDEX IF NOT EXISTS idx_app_users_email ON app_users (email)`;
  await sql/* sql */ `CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_username_lower ON app_users (lower(username))`;
}

/**
 * Bootstrap do admin:
 * - ADMIN_USERNAME (novo) + ADMIN_PASSWORD
 * - ADMIN_EMAIL continua aceito como fallback
 * - ADMIN_NAME opcional
 */
async function ensureBootstrapAdmin() {
  await ensureUsersSchema();

  const username = (process.env.ADMIN_USERNAME || "").trim().toLowerCase();
  const emailEnv = (process.env.ADMIN_EMAIL || "").trim().toLowerCase();
  const pass = process.env.ADMIN_PASSWORD || "";
  const name = (process.env.ADMIN_NAME || "Admin").trim();

  const loginId = username || emailEnv; // aceita ambos
  if (!loginId || !pass) return;

  const rows = (await sql/* sql */ `
    SELECT id, email, username, name, role, password_hash
    FROM app_users
    WHERE lower(username) = ${loginId} OR lower(email) = ${loginId}
    LIMIT 1
  `) as DbUser[];

  if (rows.length > 0) return;

  const hash = await bcrypt.hash(pass, 10);

  // email é obrigatório no schema legado; usamos emailEnv se existir, senão repetimos loginId
  const emailToStore = (emailEnv || loginId).toLowerCase();

  await sql/* sql */ `
    INSERT INTO app_users (id, email, username, name, role, password_hash)
    VALUES (${crypto.randomUUID()}, ${emailToStore}, ${loginId}, ${name}, 'admin', ${hash})
  `;
}

export const {
  handlers: { GET, POST },
  auth,
  signIn,
  signOut,
} = NextAuth({
  session: { strategy: "jwt" },

  pages: {
    signIn: "/",
  },

  providers: [
    Credentials({
      name: "Credenciais",
      credentials: {
        login: { label: "Usuário", type: "text", placeholder: "seu usuário" },
        password: { label: "Senha", type: "password" },
      },
      async authorize(credentials) {
        const login = asString(credentials?.login).trim().toLowerCase();
        const password = asString(credentials?.password);

        if (!login || !password) return null;

        await ensureUsersSchema();
        await ensureBootstrapAdmin();

        const rows = (await sql/* sql */ `
          SELECT id, email, username, name, role, password_hash
          FROM app_users
          WHERE lower(username) = ${login} OR lower(email) = ${login}
          LIMIT 1
        `) as DbUser[];

        const user = rows[0];
        if (
