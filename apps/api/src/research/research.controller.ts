import {
  Body,
  Controller,
  Delete,
  Get,
  Header,
  Headers,
  Param,
  Patch,
  Post,
  Query,
  Res,
  UseGuards,
} from "@nestjs/common";
import type { Response } from "express";
import { ResumeResearchDto } from "./dto/resume-research.dto";
import { DuplicateResearchDto, StartResearchDto } from "./dto/start-research.dto";
import { ResearchService } from "./research.service";
import { AuthGuard, OrgAdminGuard } from "../auth/auth.guard";
import { CurrentAuth, type AuthContext } from "../auth/auth.types";
import { AuthService } from "../auth/auth.service";
import { BillingService } from "../billing/billing.service";

@Controller()
export class ResearchController {
  constructor(
    private readonly research: ResearchService,
    private readonly authService: AuthService,
    private readonly billing: BillingService,
  ) {}

  @Get("/health")
  health() {
    // Liveness only — Render health checks must get 2xx even while deps warm up.
    return { ok: true, service: "kiln-api" };
  }

  @Get("/ready")
  ready() {
    return this.research.readiness();
  }

  @Get("/v1/share/:token")
  shared(@Param("token") token: string) {
    return this.research.getSharedRun(token);
  }

  @UseGuards(AuthGuard)
  @Post("/v1/research")
  start(@Body() body: StartResearchDto, @CurrentAuth() auth: AuthContext) {
    return this.research.enqueue(body.query, body.fresh === true, auth, body.llm, body.parentRunId);
  }

  @UseGuards(AuthGuard)
  @Header("Cache-Control", "no-store")
  @Get("/v1/research/:id")
  get(@Param("id") id: string, @CurrentAuth() auth: AuthContext) {
    return this.research.get(id, auth);
  }

  @UseGuards(AuthGuard)
  @Get("/v1/research/:id/graph")
  graph(@Param("id") id: string, @CurrentAuth() auth: AuthContext) {
    return this.research.evidenceGraph(id, auth);
  }

  @UseGuards(AuthGuard)
  @Post("/v1/research/:id/resume")
  resume(
    @Param("id") id: string,
    @Body() body: ResumeResearchDto,
    @CurrentAuth() auth: AuthContext,
  ) {
    return this.research.resume(id, body, auth);
  }

  @UseGuards(AuthGuard)
  @Post("/v1/research/:id/cancel")
  cancel(@Param("id") id: string, @CurrentAuth() auth: AuthContext) {
    return this.research.cancel(id, auth);
  }

  @UseGuards(AuthGuard)
  @Post("/v1/research/:id/duplicate")
  duplicate(
    @Param("id") id: string,
    @Body() body: DuplicateResearchDto,
    @CurrentAuth() auth: AuthContext,
  ) {
    return this.research.duplicate(id, auth, body?.llm);
  }

  @UseGuards(AuthGuard)
  @Delete("/v1/research/:id")
  remove(@Param("id") id: string, @CurrentAuth() auth: AuthContext) {
    return this.research.softDelete(id, auth);
  }

  @UseGuards(AuthGuard)
  @Get("/v1/research/:id/checkpoints")
  checkpoints(@Param("id") id: string, @CurrentAuth() auth: AuthContext) {
    return this.research.checkpoints(id, auth);
  }

  @UseGuards(AuthGuard)
  @Get("/v1/examples")
  examples() {
    return this.research.examples();
  }

  @UseGuards(AuthGuard)
  @Get("/v1/status")
  status() {
    return this.research.status();
  }

  @UseGuards(AuthGuard)
  @Get("/v1/research/:id/events")
  events(
    @Param("id") id: string,
    @Headers("last-event-id") lastEventId: string | undefined,
    @Res() res: Response,
    @CurrentAuth() auth: AuthContext,
  ) {
    return this.research.events(id, res, lastEventId, auth);
  }

  @UseGuards(AuthGuard)
  @Get("/v1/workspace/runs")
  workspace(
    @CurrentAuth() auth: AuthContext,
    @Query("limit") limit?: string,
    @Query("cursor") cursor?: string,
    @Query("q") q?: string,
    @Query("status") status?: string,
    @Query("archived") archived?: string,
  ) {
    return this.research.listRuns(auth, {
      limit: limit ? Number(limit) : undefined,
      cursor,
      q,
      status,
      archived: archived === "true",
    });
  }

  @UseGuards(AuthGuard)
  @Get("/v1/workspace/runs/:id/timeline")
  timeline(@Param("id") id: string, @CurrentAuth() auth: AuthContext) {
    return this.research.timeline(id, auth);
  }

  @UseGuards(AuthGuard)
  @Post("/v1/workspace/runs/:id/pin")
  pin(
    @Param("id") id: string,
    @Body() body: { pinned?: boolean },
    @CurrentAuth() auth: AuthContext,
  ) {
    return this.research.pin(id, body.pinned !== false, auth);
  }

  @UseGuards(AuthGuard)
  @Patch("/v1/workspace/runs/:id")
  patchRun(
    @Param("id") id: string,
    @Body() body: { title?: string; archived?: boolean; pinned?: boolean },
    @CurrentAuth() auth: AuthContext,
  ) {
    if (typeof body.title === "string") return this.research.rename(id, body.title, auth);
    if (typeof body.archived === "boolean") return this.research.archive(id, body.archived, auth);
    if (typeof body.pinned === "boolean") return this.research.pin(id, body.pinned, auth);
    return this.research.get(id, auth);
  }

  @UseGuards(AuthGuard)
  @Get("/v1/corpus")
  corpus() {
    return this.research.corpus();
  }

  @UseGuards(AuthGuard, OrgAdminGuard)
  @Post("/v1/corpus/refresh")
  refresh(@CurrentAuth() auth: AuthContext) {
    return this.research.refreshCorpus(auth);
  }

  @UseGuards(AuthGuard)
  @Get("/v1/knowledge")
  knowledge() {
    return this.research.knowledgeStats();
  }

  @UseGuards(AuthGuard)
  @Get("/v1/knowledge/match")
  knowledgeMatch(@Query("query") query: string) {
    return this.research.knowledgeMatch(query || "");
  }

  @UseGuards(AuthGuard)
  @Post("/v1/scenarios/serving")
  serving(@Body() body: Record<string, unknown>, @CurrentAuth() auth: AuthContext) {
    return this.research.servingScenario(body, auth);
  }

  @UseGuards(AuthGuard)
  @Post("/v1/scenarios/rag")
  rag(@Body() body: Record<string, unknown>, @CurrentAuth() auth: AuthContext) {
    return this.research.ragScenario(body, auth);
  }

  @UseGuards(AuthGuard)
  @Post("/v1/research/:id/share")
  share(
    @Param("id") id: string,
    @Body() body: { expires_in_days?: number },
    @CurrentAuth() auth: AuthContext,
  ) {
    return this.research.createShare(id, auth, body.expires_in_days || 14);
  }

  @UseGuards(AuthGuard)
  @Delete("/v1/shares/:id")
  revokeShare(@Param("id") id: string, @CurrentAuth() auth: AuthContext) {
    return this.research.revokeShare(id, auth);
  }

  @UseGuards(AuthGuard, OrgAdminGuard)
  @Get("/v1/org/export")
  exportOrg(@CurrentAuth() auth: AuthContext) {
    return this.research.exportOrg(auth);
  }

  @UseGuards(AuthGuard)
  @Get("/v1/billing")
  billingStatus(@CurrentAuth() auth: AuthContext) {
    return this.billing.getOrgBilling(auth.orgId);
  }

  @UseGuards(AuthGuard, OrgAdminGuard)
  @Post("/v1/billing/checkout")
  checkout(
    @CurrentAuth() auth: AuthContext,
    @Body() body: { success_url?: string; cancel_url?: string },
  ) {
    return this.billing.createCheckoutSession(
      auth.orgId,
      auth.email,
      body.success_url || "http://localhost:5173/settings?billing=success",
      body.cancel_url || "http://localhost:5173/settings?billing=cancel",
    );
  }

  @UseGuards(AuthGuard, OrgAdminGuard)
  @Post("/v1/billing/portal")
  portal(@CurrentAuth() auth: AuthContext, @Body() body: { return_url?: string }) {
    return this.billing.createPortalSession(
      auth.orgId,
      body.return_url || "http://localhost:5173/settings",
    );
  }

  @UseGuards(AuthGuard)
  @Get("/v1/notifications")
  notifications(@CurrentAuth() auth: AuthContext) {
    return this.research.listNotifications(auth);
  }

  @UseGuards(AuthGuard)
  @Post("/v1/notifications/:id/read")
  readNotification(@Param("id") id: string, @CurrentAuth() auth: AuthContext) {
    return this.research.markNotificationRead(id, auth);
  }

  @UseGuards(AuthGuard, OrgAdminGuard)
  @Get("/v1/org/api-keys")
  listKeys(@CurrentAuth() auth: AuthContext) {
    return this.authService.listOrgApiKeys(auth.orgId);
  }

  @UseGuards(AuthGuard, OrgAdminGuard)
  @Post("/v1/org/api-keys")
  createKey(@CurrentAuth() auth: AuthContext, @Body() body: { name?: string }) {
    return this.authService.createOrgApiKey(
      auth.orgId,
      auth.userId,
      body.name || "Default key",
    );
  }

  @UseGuards(AuthGuard, OrgAdminGuard)
  @Delete("/v1/org/api-keys/:id")
  revokeKey(@Param("id") id: string, @CurrentAuth() auth: AuthContext) {
    return this.authService.revokeOrgApiKey(auth.orgId, id);
  }

  @UseGuards(AuthGuard)
  @Post("/v1/events")
  track(
    @CurrentAuth() auth: AuthContext,
    @Body() body: { event_name: string; props?: Record<string, unknown> },
  ) {
    this.authService.trackEvent(auth.orgId, auth.userId, body.event_name, body.props || {});
    return { ok: true };
  }

  @UseGuards(AuthGuard)
  @Get("/v1/me")
  me(@CurrentAuth() auth: AuthContext) {
    return auth;
  }
}
