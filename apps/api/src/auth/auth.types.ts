import { createParamDecorator, ExecutionContext } from "@nestjs/common";
import type { Request } from "express";

export type OrgRole = "org:admin" | "org:member" | "org:viewer";

export type AuthContext = {
  userId: string;
  orgId: string;
  role: OrgRole;
  email?: string;
  name?: string;
  authMode: "clerk" | "dev" | "api_key";
  correlationId: string;
};

export type AuthedRequest = Request & {
  auth?: AuthContext;
  correlationId?: string;
};

export const CurrentAuth = createParamDecorator(
  (_data: unknown, ctx: ExecutionContext): AuthContext => {
    const req = ctx.switchToHttp().getRequest<AuthedRequest>();
    if (!req.auth) {
      throw new Error("Auth context missing");
    }
    return req.auth;
  },
);

export function isOrgAdmin(role: string | undefined): boolean {
  return role === "org:admin" || role === "admin";
}
