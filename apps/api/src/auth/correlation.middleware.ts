import { Injectable, NestMiddleware } from "@nestjs/common";
import type { NextFunction, Response } from "express";
import { randomUUID } from "crypto";
import type { AuthedRequest } from "./auth.types";

@Injectable()
export class CorrelationMiddleware implements NestMiddleware {
  use(req: AuthedRequest, res: Response, next: NextFunction) {
    const id = req.header("x-correlation-id") || randomUUID();
    req.correlationId = id;
    res.setHeader("x-correlation-id", id);
    next();
  }
}
