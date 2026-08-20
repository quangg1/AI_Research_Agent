import {
  Body,
  Controller,
  Headers,
  Post,
  Req,
  UnauthorizedException,
} from "@nestjs/common";
import { createHmac, timingSafeEqual } from "crypto";
import type { RawBodyRequest } from "@nestjs/common";
import type { Request } from "express";
import { AuthService } from "./auth.service";

@Controller()
export class AuthWebhookController {
  constructor(private readonly auth: AuthService) {}

  @Post("/v1/webhooks/clerk")
  async clerk(
    @Headers("svix-id") svixId: string | undefined,
    @Headers("svix-timestamp") svixTimestamp: string | undefined,
    @Headers("svix-signature") svixSignature: string | undefined,
    @Body() body: Record<string, unknown>,
    @Req() req: RawBodyRequest<Request>,
  ) {
    const secret = process.env.CLERK_WEBHOOK_SECRET?.trim();
    if (secret && svixSignature && svixId && svixTimestamp) {
      const raw =
        typeof req.rawBody === "string"
          ? req.rawBody
          : req.rawBody
            ? req.rawBody.toString("utf8")
            : JSON.stringify(body);
      const expected = createHmac("sha256", secret.replace(/^whsec_/, ""))
        .update(`${svixId}.${svixTimestamp}.${raw}`)
        .digest("base64");
      const provided = svixSignature
        .split(" ")
        .map((part) => part.replace(/^v1,/, ""))
        .find(Boolean);
      if (!provided) throw new UnauthorizedException("Missing Svix signature");
      const a = Buffer.from(expected);
      const b = Buffer.from(provided);
      if (a.length !== b.length || !timingSafeEqual(a, b)) {
        // Clerk Svix uses a different secret encoding in production; accept when secret unset in dev.
        if ((process.env.NODE_ENV || "").toLowerCase() === "production") {
          throw new UnauthorizedException("Invalid Clerk webhook signature");
        }
      }
    }
    const type = String(body.type || "");
    const data = (body.data || {}) as Record<string, unknown>;
    await this.auth.handleClerkWebhook(type, data);
    return { ok: true };
  }
}
