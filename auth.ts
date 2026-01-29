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

function synthEmail(loginId: string): string {
  // Como o schema legado exige email NOT NULL UNIQUE:
  // - se tiver ADMIN_EMAIL, usa ele
  // - se loginId for email, usa ele
  // - senão cria um email sintético estável (evita conflito)
  if (loginId.includes("@")) return loginId.toLowerCase();
  return `${loginId.toLowerCase()}@local.invalid`;
}

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

  await sql/* sql */ `ALTER TABLE app_users ADD COLUMN IF NOT EXISTS username text`;
  await sql/* sql */ `UPDATE app_users SET username = email WHERE username IS NULL`;

  await sql/* sql */ `CREATE INDEX IF NOT EXISTS idx_app_users_email ON app_users (email)`;
  await sql/* sql */ `CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_username_lower ON app_users (lower(username))`;
}

/**
 * Bootstrap do admin:
 * - ADMIN_USERNAME + ADMIN_PASSWORD (recomendado)
 * - ADMIN_EMAIL continua aceito como fallback
 * Regra importante:
 * - Se o email já existir, atualiza o usuário existente para ser admin e/ou define username.
 */
async function ensureBootstrapAdmin() {
  await ensureUsersSchema();

  const usernameEnv = (process.env.ADMIN_USERNAME || "").trim().toLowerCase();
  const emailEnv = (process.env.ADMIN_EMAIL || "").trim().toLowerCase();
  const pass = process.env.ADMIN_PASSWORD || "";
  const name = (process.env.ADMIN_NAME || "Admin").trim();

  const loginId = usernameEnv || emailEnv; // login preferencial
  if (!loginId || !pass) return;

  const hash = await bcrypt.hash(pass, 10);

  // 1) Procura por username/email = loginId
  const byLogin = (await sql/* sql */ `
    SELECT id, email, username, name, role, password_hash
    FROM app_users
    WHERE lower(username) = ${loginId} OR lower(email) = ${loginId}
    LIMIT 1
  `) as DbUser[];

  if (byLogin.length > 0) {
    const u = byLogin[0];

    // garante username, role admin, nome e senha atualizada
    await sql/* sql */ `
      UPDATE app_users
      SET
        username = COALESCE(username, ${loginId}),
        role = 'admin',
        name = COALESCE(name, ${name}),
        password_hash = ${hash}
      WHERE id = ${u.id}
    `;
    return;
  }

  // 2) Se tiver ADMIN_EMAIL, pode existir usuário com esse email (mesmo que loginId seja outro)
  if (emailEnv) {
    const byEmail = (await sql/* sql */ `
      SELECT id, email, username, name, role, password_hash
      FROM app_users
      WHERE lower(email) = ${emailEnv}
      LIMIT 1
    `) as DbUser[];

    if (byEmail.length > 0) {
      const u = byEmail[0];

      await sql/* sql */ `
        UPDATE app_users
        SET
          username = ${loginId},
          role = 'admin',
          name = COALESCE(name, ${name}),
          password_hash = ${hash}
        WHERE id = ${u.id}
      `;
      return;
    }
  }

  // 3) Não existe: cria novo
  const emailToStore = emailEnv ? emailEnv : synthEmail(loginId);

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
        if (!user) return null;

        const ok = await bcrypt.compare(password, user.password_hash);
        if (!ok) return null;

        try {
          await sql/* sql */ `UPDATE app_users SET last_login_at = now() WHERE id = ${user.id}`;
        } catch {
          // ignore
        }

        return {
          id: user.id,
          email: user.email,
          name: user.name || user.username || user.email,
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

    authorized({ auth, request }) {
      const { pathname } = request.nextUrl;

      if (pathname === "/") return true;
      if (pathname.startsWith("/api/auth")) return true;

      if (pathname.startsWith("/_next")) return true;
      if (pathname === "/favicon.ico") return true;

      return !!auth?.user;
    },
  },
});
