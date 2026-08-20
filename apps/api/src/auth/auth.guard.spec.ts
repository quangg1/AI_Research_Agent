import { AuthGuard } from "../auth/auth.guard";
import { AuthService } from "../auth/auth.service";

describe("AuthGuard tenancy headers (dev mode)", () => {
  const previous = process.env.AUTH_MODE;

  beforeAll(() => {
    process.env.AUTH_MODE = "dev";
  });

  afterAll(() => {
    process.env.AUTH_MODE = previous;
  });

  it("attaches org and user from dev headers", async () => {
    const ensureUser = jest.fn();
    const ensureOrg = jest.fn();
    const ensureMembership = jest.fn();
    const authService = {
      ensureUser,
      ensureOrg,
      ensureMembership,
    } as unknown as AuthService;
    const guard = new AuthGuard(authService);
    const req: Record<string, unknown> = {
      header: (name: string) => {
        const map: Record<string, string> = {
          "x-dev-user-id": "user_a",
          "x-dev-org-id": "org_a",
          "x-dev-role": "org:admin",
        };
        return map[name.toLowerCase()];
      },
      query: {},
    };
    const ctx = {
      switchToHttp: () => ({
        getRequest: () => req,
      }),
    };
    await expect(guard.canActivate(ctx as never)).resolves.toBe(true);
    expect(req.auth).toMatchObject({
      userId: "user_a",
      orgId: "org_a",
      role: "org:admin",
      authMode: "dev",
    });
    expect(ensureMembership).toHaveBeenCalledWith("org_a", "user_a", "org:admin");
  });
});
