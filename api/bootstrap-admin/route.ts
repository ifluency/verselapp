import { NextResponse } from "next/server";
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

function getBearerToken(req: Request): string {
  const auth = req.headers.get("authorization") || "";
  if (auth.toLowerCase().startsWith("bearer ")) return auth.slice(7).trim();
  const x = req.headers.get("x-bootstrap-token");
  return (x || "").trim();
}

function timingSafeEqual(a: string, b: string): boolean {
  // evita timing attacks
  const aa = Buffer.from(a);
  const bb = Buffer.from(b);
  if (aa.length !== bb.length) return false;
  return crypto.timingSafeEqual(aa, bb);
}

function synthEmail(loginId: string): string {
  if (loginId.includes("@")) return loginId.toLowerCase();
  return `${loginId.toLowerCase()}@local.invalid`;
}

async function ensureUsersSchema(sql: ReturnType<typeof neon>) {
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

async function bootstrapAdmin(sql: ReturnType<typeof neon>) {
  const usernameEnv = (process.env.ADMIN_USERNAME || "").trim().toLowerCase();
  const emailEnv = (process.env.ADMIN_EMAIL || "").trim().toLowerCase();
  const pass = process.env.ADMIN_PASSWORD || "";
  const name = (process.env.ADMIN_NAME || "Admin").trim();

  const loginId = usernameEnv || emailEnv;
  if (!loginId || !pass) {
    throw new Error("ADMIN_USERNAME/ADMIN_EMAIL e ADMIN_PASSWORD precisam estar definidos.");
  }

  await ensureUsersSchema(sql);

  const hash = await bcrypt.hash(pass, 10);

  // 1) tenta achar por username/email = loginId
  const byLogin = (await sql/* sql */ `
    SELECT id, email, username, name, role, password_hash
    FROM app_users
    WHERE lower(username) = ${loginId} OR lower(email) = ${loginId}
    LIMIT 1
  `) as DbUser[];

  if (byLogin.length > 0) {
    const u = byLogin[0];
    await sql/* sql */ `
      UPDATE app_users
      SET
        username = COALESCE(username, ${loginId}),
        role = 'admin',
        name = COALESCE(name, ${name}),
        password_hash = ${hash}
      WHERE id = ${u.id}
    `;
    return { mode: "updated_by_login", id: u.id };
  }

  // 2) se tiver ADMIN_EMAIL e já existir, atualiza esse registro
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
      return { mode: "updated_by_email", id: u.id };
    }
  }

  // 3) cria novo
  const emailToStore = emailEnv ? emailEnv : synthEmail(loginId);

  await sql/* sql */ `
    INSERT INTO app_users (id, email, username, name, role, password_hash)
    VALUES (${crypto.randomUUID()}, ${emailToStore}, ${loginId}, ${name}, 'admin', ${hash})
  `;

  return { mode: "inserted_new" };
}

export async function POST(req: Request) {
  // Token de bootstrap — se não existir, escondemos a rota
  const expected = (process.env.ADMIN_BOOTSTRAP_TOKEN || "").trim();
  if (!expected) {
    return new NextResponse("Not Found", { status: 404 });
  }

  const provided = getBearerToken(req);
  if (!provided || !timingSafeEqual(provided, expected)) {
    return NextResponse.json({ ok: false, error: "forbidden" }, { status: 403 });
  }

  const dbUrl = process.env.DATABASE_URL || "";
  if (!dbUrl) {
    return NextResponse.json({ ok: false, error: "DATABASE_URL ausente" }, { status: 500 });
  }

  try {
    const sql = neon(dbUrl);
    const result = await bootstrapAdmin(sql);

    return NextResponse.json({
      ok: true,
      result,
      admin_username: (process.env.ADMIN_USERNAME || process.env.ADMIN_EMAIL || "").trim(),
    });
  } catch (e: any) {
    return NextResponse.json(
      { ok: false, error: e?.message || "bootstrap_failed" },
      { status: 500 }
    );
  }
}
