import { Inject, Injectable, Logger } from "@nestjs/common";
import Stripe from "stripe";
import type { Pool } from "pg";
import { DATABASE_POOL } from "../research/research.repository";

const PLAN_QUOTAS: Record<string, number> = {
  free: 50,
  pro: 500,
  team: 5000,
};

@Injectable()
export class BillingService {
  private readonly logger = new Logger(BillingService.name);
  private stripe: Stripe | null = null;

  constructor(@Inject(DATABASE_POOL) private readonly pool: Pool) {
    const key = process.env.STRIPE_SECRET_KEY?.trim();
    if (key) this.stripe = new Stripe(key);
  }

  private client(): Stripe {
    if (!this.stripe) throw new Error("STRIPE_SECRET_KEY is not configured");
    return this.stripe;
  }

  async getOrgBilling(orgId: string) {
    const org = await this.pool.query(
      `SELECT id, name, plan, monthly_run_quota, retention_days,
              stripe_customer_id, stripe_subscription_id, billing_status
       FROM organizations WHERE id=$1`,
      [orgId],
    );
    if (!org.rowCount) throw new Error("organization not found");
    const usage = await this.pool.query<{ n: number }>(
      `SELECT count(*)::int AS n FROM usage_ledger
       WHERE org_id=$1 AND created_at >= date_trunc('month', NOW())`,
      [orgId],
    );
    const row = org.rows[0];
    return {
      ...row,
      usage_this_month: usage.rows[0]?.n || 0,
      remaining: Math.max(0, Number(row.monthly_run_quota) - (usage.rows[0]?.n || 0)),
    };
  }

  async assertWithinQuota(orgId: string) {
    const billing = await this.getOrgBilling(orgId);
    if (billing.remaining <= 0) {
      const err = new Error(
        `Monthly run quota exceeded (${billing.usage_this_month}/${billing.monthly_run_quota} on plan ${billing.plan}). Upgrade in Settings.`,
      );
      (err as Error & { status: number }).status = 402;
      throw err;
    }
    return billing;
  }

  async recordUsage(orgId: string, userId: string, runId: string, tokens = 0) {
    await this.pool.query(
      `INSERT INTO usage_ledger (org_id, run_id, user_id, kind, tokens, cost_estimate_usd)
       VALUES ($1, $2, $3, 'research_run', $4, $5)`,
      [orgId, runId, userId, tokens, tokens * 0.000002],
    );
  }

  async createCheckoutSession(orgId: string, userEmail: string | undefined, successUrl: string, cancelUrl: string) {
    const priceId = process.env.STRIPE_PRICE_PRO?.trim();
    if (!priceId) throw new Error("STRIPE_PRICE_PRO is not configured");
    const org = await this.pool.query<{ stripe_customer_id: string | null; name: string }>(
      `SELECT stripe_customer_id, name FROM organizations WHERE id=$1`,
      [orgId],
    );
    if (!org.rowCount) throw new Error("organization not found");
    let customerId = org.rows[0].stripe_customer_id;
    if (!customerId) {
      const customer = await this.client().customers.create({
        email: userEmail,
        name: org.rows[0].name,
        metadata: { org_id: orgId },
      });
      customerId = customer.id;
      await this.pool.query(
        `UPDATE organizations SET stripe_customer_id=$2, updated_at=NOW() WHERE id=$1`,
        [orgId, customerId],
      );
    }
    const session = await this.client().checkout.sessions.create({
      mode: "subscription",
      customer: customerId,
      line_items: [{ price: priceId, quantity: 1 }],
      success_url: successUrl,
      cancel_url: cancelUrl,
      metadata: { org_id: orgId },
      subscription_data: { metadata: { org_id: orgId } },
    });
    return { url: session.url, id: session.id };
  }

  async createPortalSession(orgId: string, returnUrl: string) {
    const org = await this.pool.query<{ stripe_customer_id: string | null }>(
      `SELECT stripe_customer_id FROM organizations WHERE id=$1`,
      [orgId],
    );
    if (!org.rowCount || !org.rows[0].stripe_customer_id) {
      throw new Error("No Stripe customer for this organization");
    }
    const session = await this.client().billingPortal.sessions.create({
      customer: org.rows[0].stripe_customer_id,
      return_url: returnUrl,
    });
    return { url: session.url };
  }

  async handleStripeWebhook(rawBody: Buffer, signature: string) {
    const secret = process.env.STRIPE_WEBHOOK_SECRET?.trim();
    if (!secret) throw new Error("STRIPE_WEBHOOK_SECRET is not configured");
    const event = this.client().webhooks.constructEvent(rawBody, signature, secret);
    await this.applyStripeEvent(event);
    return { ok: true };
  }

  async applyStripeEvent(event: Stripe.Event) {
    this.logger.log(`Stripe event: ${event.type}`);
    if (event.type === "checkout.session.completed") {
      const session = event.data.object as Stripe.Checkout.Session;
      const orgId = session.metadata?.org_id;
      if (!orgId) return;
      await this.setPlan(orgId, "pro", session.subscription ? String(session.subscription) : null, "active");
    }
    if (
      event.type === "customer.subscription.updated" ||
      event.type === "customer.subscription.created"
    ) {
      const sub = event.data.object as Stripe.Subscription;
      const orgId = sub.metadata?.org_id;
      if (!orgId) return;
      const status = sub.status === "active" || sub.status === "trialing" ? "active" : sub.status;
      const plan = status === "active" ? "pro" : "free";
      await this.setPlan(orgId, plan, sub.id, status);
    }
    if (event.type === "customer.subscription.deleted") {
      const sub = event.data.object as Stripe.Subscription;
      const orgId = sub.metadata?.org_id;
      if (!orgId) return;
      await this.setPlan(orgId, "free", null, "canceled");
    }
  }

  private async setPlan(
    orgId: string,
    plan: string,
    subscriptionId: string | null,
    billingStatus: string,
  ) {
    const quota = PLAN_QUOTAS[plan] ?? PLAN_QUOTAS.free;
    await this.pool.query(
      `UPDATE organizations
       SET plan=$2, monthly_run_quota=$3, stripe_subscription_id=$4,
           billing_status=$5, updated_at=NOW()
       WHERE id=$1`,
      [orgId, plan, quota, subscriptionId, billingStatus],
    );
  }
}
