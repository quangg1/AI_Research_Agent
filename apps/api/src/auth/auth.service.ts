import { Injectable, Logger } from "@nestjs/common";
import { createHash, randomBytes, randomUUID } from "crypto";
import { verifyToken } from "@clerk/backend";
import { Inject } from "@nestjs/common";
import { DATABASE_POOL } from "../research/research.repository";
import type { Pool } from "pg";
import type { AuthContext, OrgRole } from "./auth.types";

@Injectable()
export class AuthService {
  private readonly logger = new Logger(AuthService.name);

  constructor(@Inject(DATABASE_POOL) private readonly pool: Pool) {}

  async ensureUser(user: {
    id: string;
    email?: string | null;
    name?: string | null;
    imageUrl?: string | null;
  }) {
    await this.pool.query(
      `INSERT INTO users (id, email, name, image_url)
       VALUES ($1, $2, $3, $4)
       ON CONFLICT (id) DO UPDATE SET
         email = COALESCE(EXCLUDED.email, users.email),
         name = COALESCE(EXCLUDED.name, users.name),
         image_url = COALESCE(EXCLUDED.image_url, users.image_url),
         updated_at = NOW()`,
      [user.id, user.email || null, user.name || null, user.imageUrl || null],
    );
  }

  async ensureOrg(org: {
    id: string;
    name: string;
    slug?: string | null;
  }) {
    await this.pool.query(
      `INSERT INTO organizations (id, name, slug)
       VALUES ($1, $2, $3)
       ON CONFLICT (id) DO UPDATE SET
         name = EXCLUDED.name,
         slug = COALESCE(EXCLUDED.slug, organizations.slug),
         updated_at = NOW()`,
      [org.id, org.name, org.slug || null],
    );
  }

  async ensureMembership(orgId: string, userId: string, role: OrgRole = "org:member") {
    await this.pool.query(
      `INSERT INTO memberships (org_id, user_id, role)
       VALUES ($1, $2, $3)
       ON CONFLICT (org_id, user_id) DO UPDATE SET
         role = EXCLUDED.role,
         updated_at = NOW()`,
      [orgId, userId, role],
    );
  }

  async removeMembership(orgId: string, userId: string) {
    await this.pool.query(`DELETE FROM memberships WHERE org_id=$1 AND user_id=$2`, [
      orgId,
      userId,
    ]);
  }

  async getMembership(orgId: string, userId: string) {
    const result = await this.pool.query<{ role: string }>(
      `SELECT role FROM memberships WHERE org_id=$1 AND user_id=$2`,
      [orgId, userId],
    );
    return result.rows[0] || null;
  }

  async resolveClerkToken(token: string, orgIdHeader?: string): Promise<AuthContext> {
    const secretKey = process.env.CLERK_SECRET_KEY?.trim();
    if (!secretKey) {
      throw new Error("CLERK_SECRET_KEY is not configured");
    }
    const payload = await verifyToken(token, { secretKey });
    const userId = String(payload.sub || "");
    if (!userId) throw new Error("Invalid Clerk token: missing subject");

    const orgId =
      orgIdHeader ||
      (typeof payload.org_id === "string" ? payload.org_id : undefined) ||
      (typeof (payload as { o?: { id?: string } }).o?.id === "string"
        ? (payload as { o: { id: string } }).o.id
        : undefined);

    if (!orgId) {
      throw new Error("Active organization is required. Select an organization in Clerk.");
    }

    const roleClaim =
      (typeof payload.org_role === "string" && payload.org_role) ||
      (typeof (payload as { o?: { rol?: string } }).o?.rol === "string"
        ? `org:${(payload as { o: { rol: string } }).o.rol}`
        : "org:member");

    const role = (roleClaim.startsWith("org:") ? roleClaim : `org:${roleClaim}`) as OrgRole;

    await this.ensureUser({
      id: userId,
      email: typeof payload.email === "string" ? payload.email : undefined,
      name: typeof payload.name === "string" ? payload.name : undefined,
    });
    await this.ensureOrg({ id: orgId, name: orgId });
    await this.ensureMembership(orgId, userId, role);

    return {
      userId,
      orgId,
      role,
      email: typeof payload.email === "string" ? payload.email : undefined,
      authMode: "clerk",
      correlationId: "",
    };
  }

  async resolveApiKey(rawKey: string): Promise<AuthContext | null> {
    const hash = createHash("sha256").update(rawKey).digest("hex");
    const result = await this.pool.query<{
      id: string;
      org_id: string;
      created_by: string | null;
      scopes: string[];
    }>(
      `SELECT id, org_id, created_by, scopes FROM org_api_keys
       WHERE key_hash=$1 AND revoked_at IS NULL`,
      [hash],
    );
    if (!result.rowCount) return null;
    const row = result.rows[0];
    await this.pool.query(`UPDATE org_api_keys SET last_used_at=NOW() WHERE id=$1`, [row.id]);
    return {
      userId: row.created_by || `apikey:${row.id}`,
      orgId: row.org_id,
      role: "org:member",
      authMode: "api_key",
      correlationId: "",
    };
  }

  async createOrgApiKey(orgId: string, userId: string, name: string) {
    const id = randomUUID();
    const secret = `kiln_${randomBytes(24).toString("hex")}`;
    const prefix = secret.slice(0, 12);
    const hash = createHash("sha256").update(secret).digest("hex");
    await this.pool.query(
      `INSERT INTO org_api_keys (id, org_id, name, key_prefix, key_hash, created_by)
       VALUES ($1, $2, $3, $4, $5, $6)`,
      [id, orgId, name, prefix, hash, userId],
    );
    return { id, name, prefix, secret, created_at: new Date().toISOString() };
  }

  async listOrgApiKeys(orgId: string) {
    const result = await this.pool.query(
      `SELECT id, name, key_prefix, scopes, created_by, last_used_at, created_at, revoked_at
       FROM org_api_keys WHERE org_id=$1 ORDER BY created_at DESC`,
      [orgId],
    );
    return result.rows;
  }

  async revokeOrgApiKey(orgId: string, keyId: string) {
    const result = await this.pool.query(
      `UPDATE org_api_keys SET revoked_at=NOW()
       WHERE id=$1 AND org_id=$2 AND revoked_at IS NULL`,
      [keyId, orgId],
    );
    return Boolean(result.rowCount);
  }

  async handleClerkWebhook(eventType: string, data: Record<string, unknown>) {
    this.logger.log(`Clerk webhook: ${eventType}`);
    if (eventType.startsWith("user.")) {
      const id = String(data.id || "");
      if (!id) return;
      const emails = data.email_addresses as Array<{ email_address?: string }> | undefined;
      await this.ensureUser({
        id,
        email: emails?.[0]?.email_address,
        name:
          [data.first_name, data.last_name].filter(Boolean).join(" ") ||
          (typeof data.username === "string" ? data.username : null),
        imageUrl: typeof data.image_url === "string" ? data.image_url : null,
      });
      if (eventType === "user.deleted") {
        // keep row for FK integrity; no hard delete
      }
    }
    if (eventType.startsWith("organization.")) {
      const id = String(data.id || "");
      if (!id) return;
      if (eventType === "organization.deleted") return;
      await this.ensureOrg({
        id,
        name: String(data.name || id),
        slug: typeof data.slug === "string" ? data.slug : null,
      });
    }
    if (eventType.startsWith("organizationMembership.")) {
      const org = data.organization as { id?: string } | undefined;
      const user = data.public_user_data as { user_id?: string } | undefined;
      const orgId = org?.id;
      const userId = user?.user_id || String((data as { user_id?: string }).user_id || "");
      if (!orgId || !userId) return;
      if (eventType.endsWith("deleted")) {
        await this.removeMembership(orgId, userId);
        return;
      }
      const role = String(data.role || "org:member") as OrgRole;
      await this.ensureUser({ id: userId });
      await this.ensureOrg({ id: orgId, name: orgId });
      await this.ensureMembership(orgId, userId, role);
    }
  }

  trackEvent(orgId: string | null, userId: string | null, eventName: string, props: Record<string, unknown> = {}) {
    void this.pool
      .query(
        `INSERT INTO product_events (org_id, user_id, event_name, props)
         VALUES ($1, $2, $3, $4::jsonb)`,
        [orgId, userId, eventName, JSON.stringify(props)],
      )
      .catch((err) => this.logger.warn(`product event failed: ${(err as Error).message}`));
  }
}
