import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import { neon } from "@neondatabase/serverless";
import bcrypt from "bcryptjs";
import crypto from "crypto";

type DbUser = {
  id: string;
  email: string;
  name: string | null;
  role: string;
  password_hash: string;
};

const sql = neon(process.env.DATABASE_URL || "");

/**
 * Tabela simples de usuários (fora do schema do NextAuth).
 * - Mantém compatibilidade com Neon (Postgres)
 * - Evita precisar de Adapter/Prisma
 */
async function ensureUsersSchema() {
  if (!process.env.DATABASE_URL) throw new Error("DATABASE_URL não configurada.");

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
  await sql/* sql */ `CREATE INDEX IF NOT EXISTS idx_app_users_email ON app_users (email)`;
}

/**
 * Bootstrap de um admin via env vars (primeiro setup).
 * Defina:
 *  - ADMIN_EMAIL
 *  - ADMIN_PASSWORD
 * Opcional:
 *  - ADMIN_NAME
 */
async function ensureBootstrapAdmin() {
  const email = (process.env.ADMIN_EMAIL || "").trim().toLowerCase();
  const pass = process.env.ADMIN_PASSWORD || "";
  const name = (process.env.ADMIN_NAME || "Admin").trim();

  if (!email || !pass) return;

  const rows = await sql<DbUser[]>/* sql */ `
    SELECT id, email, name, role, password_hash
    FROM app_users
    WHERE lower(email) = ${email}
    LIMIT 1
  `;
  if (rows.length > 0) return;

  const hash = await bcrypt.hash(pass, 10);
  await sql/* sql */ `
    INSERT INTO app_users (id, email, name, role, password_hash)
    VALUES (${crypto.randomUUID()}, ${email}, ${name}, 'admin', ${hash})
  `;
}

export const {
  handlers: { GET, POST },
  auth,
  signIn,
  signOut,
} = NextAuth({
  // jwt (sem adapter) -> middleware não precisa bater no DB
  session: { strategy: "jwt" },

  pages: {
    signIn: "/", // nossa página de login é a home
  },

  providers: [
    Credentials({
      name: "Credenciais",
      credentials: {
        email: { label: "Email", type: "text", placeholder: "seu@email" },
        password: { label: "Senha", type: "password" },
      },
      async authorize(credentials) {
        const email = (credentials?.email || "").trim().toLowerCase();
        const password = credentials?.password || "";

        if (!email || !password) return null;

        await ensureUsersSchema();
        await ensureBootstrapAdmin();

        const rows = await sql<DbUser[]>/* sql */ `
          SELECT id, email, name, role, password_hash
          FROM app_users
          WHERE lower(email) = ${email}
          LIMIT 1
        `;

        const user = rows[0];
        if (!user) return null;

        const ok = await bcrypt.compare(password, user.password_hash);
        if (!ok) return null;

        // atualiza last_login_at (best-effort)
        try {
          await sql/* sql */ `UPDATE app_users SET last_login_at = now() WHERE id = ${user.id}`;
        } catch {
          // ignore
        }

        return {
          id: user.id,
          email: user.email,
          name: user.name || user.email,
          // @ts-ignore
          role: user.role,
        };
      },
    }),
  ],

  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        token.id = (user as any).id;
        token.role = (user as any).role || "user";
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        // @ts-ignore
        session.user.id = token.id;
        // @ts-ignore
        session.user.role = token.role;
      }
      return session;
    },

    /**
     * Protege rotas via middleware (usado quando auth() roda no middleware).
     * Mantém "/" e "/api/auth/*" públicos; demais precisam de sessão.
     */
    authorized({ auth, request }) {
      const { pathname } = request.nextUrl;

      if (pathname === "/") return true;
      if (pathname.startsWith("/api/auth")) return true;

      // Next internals
      if (pathname.startsWith("/_next")) return true;
      if (pathname === "/favicon.ico") return true;

      return !!auth?.user;
    },
  },
});
