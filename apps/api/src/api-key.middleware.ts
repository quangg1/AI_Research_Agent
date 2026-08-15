import { Injectable, NestMiddleware, UnauthorizedException } from "@nestjs/common";
import { timingSafeEqual } from "crypto";
import type { NextFunction, Request, Response } from "express";

@Injectable()
export class ApiKeyMiddleware implements NestMiddleware {
  use(req: Request, _res: Response, next: NextFunction) {
    if (req.query.api_key !== undefined) {
      throw new UnauthorizedException("Query-string API keys are not accepted.");
    }
    if ((process.env.AUTH_MODE || "").toLowerCase() === "disabled") {
      next();
      return;
    }
    const expected = process.env.API_SHARED_KEY?.trim();
    if (!expected) {
      throw new UnauthorizedException("API authentication is not configured.");
    }

    const header = req.header("x-api-key");
    const authorization = req.header("authorization");
    const bearer = authorization?.startsWith("Bearer ") ? authorization.slice(7) : undefined;
    const supplied = header || bearer || "";
    const expectedBuffer = Buffer.from(expected);
    const suppliedBuffer = Buffer.from(supplied);

    if (
      expectedBuffer.length !== suppliedBuffer.length ||
      !timingSafeEqual(expectedBuffer, suppliedBuffer)
    ) {
      throw new UnauthorizedException("A valid API key is required.");
    }
    next();
  }
}
