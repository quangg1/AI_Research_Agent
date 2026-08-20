import {
  Controller,
  Headers,
  Post,
  Req,
  Res,
  UnauthorizedException,
} from "@nestjs/common";
import type { RawBodyRequest } from "@nestjs/common";
import type { Request, Response } from "express";
import { BillingService } from "./billing.service";

@Controller()
export class BillingWebhookController {
  constructor(private readonly billing: BillingService) {}

  @Post("/v1/webhooks/stripe")
  async stripe(
    @Headers("stripe-signature") signature: string | undefined,
    @Req() req: RawBodyRequest<Request>,
    @Res() res: Response,
  ) {
    if (!signature) throw new UnauthorizedException("Missing Stripe signature");
    const raw = req.rawBody;
    if (!raw) throw new UnauthorizedException("Raw body required for Stripe webhooks");
    try {
      const result = await this.billing.handleStripeWebhook(raw, signature);
      res.json(result);
    } catch (err) {
      throw new UnauthorizedException((err as Error).message);
    }
  }
}
