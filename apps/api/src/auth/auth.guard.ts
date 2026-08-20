import {
  CanActivate,
  ExecutionContext,
  Injectable,
  UnauthorizedException,
  ForbiddenException,
} from "@nestjs/common";
import { AuthService } from "./auth.service";
import type { AuthedRequest, OrgRole } from "./auth.types";
import { isOrgAdmin } from "./auth.types";
import { randomUUID } from "crypto";

@Injectable()
export class AuthGuard implements CanActivate {
  constructor(private readonly auth: AuthService) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const req = context.switchToHttp().getRequest<AuthedRequest>();
    const correlationId = req.header("x-correlation-id") || randomUUID();
    req.correlationId = correlationId;

    const mode = (process.env.AUTH_MODE || "clerk").toLowerCase();

    if (mode === "dev") {
      const userId = req.header("x-dev-user-id") || "user_dev";
      const orgId = req.header("x-dev-org-id") || "org_default";
      const role = (req.header("x-dev-role") || "org:admin") as OrgRole;
      await this.auth.ensureUser({ id: userId, email: `${userId}@localhost`, name: userId });
      await this.auth.ensureOrg({ id: orgId, name: orgId === "org_default" ? "Default Org" : orgId });
      await this.auth.ensureMembership(orgId, userId, role);
      req.auth = {
        userId,
        orgId,
        role,
        authMode: "dev",
        correlationId,
      };
      return true;
    }

    if (mode === "disabled") {
      if ((process.env.NODE_ENV || "").toLowerCase() === "production") {
        throw new UnauthorizedException("AUTH_MODE=disabled is not allowed in production");
      }
      await this.auth.ensureUser({ id: "user_dev", email: "dev@localhost", name: "Dev User" });
      await this.auth.ensureOrg({ id: "org_default", name: "Default Org", slug: "default" });
      await this.auth.ensureMembership("org_default", "user_dev", "org:admin");
      req.auth = {
        userId: "user_dev",
        orgId: "org_default",
        role: "org:admin",
        authMode: "dev",
        correlationId,
      };
      return true;
    }

    // clerk (default) or key-compatible: prefer Bearer Clerk JWT, else org API key, else reject query api_key
    if (req.query.api_key !== undefined) {
      throw new UnauthorizedException("Query-string API keys are not accepted.");
    }

    const authorization = req.header("authorization");
    const bearer = authorization?.startsWith("Bearer ") ? authorization.slice(7) : undefined;
    const xApiKey = req.header("x-api-key");

    if (bearer && !bearer.startsWith("kiln_")) {
      try {
        const auth = await this.auth.resolveClerkToken(bearer, req.header("x-org-id") || undefined);
        auth.correlationId = correlationId;
        req.auth = auth;
        return true;
      } catch (err) {
        throw new UnauthorizedException((err as Error).message || "Invalid session");
      }
    }

    const apiKey = bearer?.startsWith("kiln_") ? bearer : xApiKey;
    if (apiKey) {
      const auth = await this.auth.resolveApiKey(apiKey);
      if (!auth) throw new UnauthorizedException("Invalid API key");
      auth.correlationId = correlationId;
      req.auth = auth;
      return true;
    }

    throw new UnauthorizedException("Authentication required");
  }
}

@Injectable()
export class OrgAdminGuard implements CanActivate {
  canActivate(context: ExecutionContext): boolean {
    const req = context.switchToHttp().getRequest<AuthedRequest>();
    if (!req.auth || !isOrgAdmin(req.auth.role)) {
      throw new ForbiddenException("Organization admin role required");
    }
    return true;
  }
}
