import { Body, Controller, Get, Headers, Param, Post, Query, Res } from "@nestjs/common";
import type { Response } from "express";
import { ResumeResearchDto } from "./dto/resume-research.dto";
import { StartResearchDto } from "./dto/start-research.dto";
import { ResearchService } from "./research.service";

@Controller()
export class ResearchController {
  constructor(private readonly research: ResearchService) {}

  @Get("/health")
  health() {
    return this.research.readiness();
  }

  @Post("/v1/research")
  start(@Body() body: StartResearchDto) {
    return this.research.enqueue(body.query, body.fresh === true);
  }

  @Get("/v1/research/:id")
  get(@Param("id") id: string) {
    return this.research.get(id);
  }

  @Post("/v1/research/:id/resume")
  resume(
    @Param("id") id: string,
    @Body() body: ResumeResearchDto,
  ) {
    return this.research.resume(id, body);
  }

  @Get("/v1/research/:id/checkpoints")
  checkpoints(@Param("id") id: string) {
    return this.research.checkpoints(id);
  }

  @Get("/v1/examples")
  examples() {
    return this.research.examples();
  }

  @Get("/v1/status")
  status() {
    return this.research.status();
  }

  @Get("/v1/research/:id/events")
  events(
    @Param("id") id: string,
    @Headers("last-event-id") lastEventId: string | undefined,
    @Res() res: Response,
  ) {
    return this.research.events(id, res, lastEventId);
  }

  @Get("/v1/workspace/runs")
  workspace() {
    return this.research.listRuns();
  }

  @Get("/v1/workspace/runs/:id/timeline")
  timeline(@Param("id") id: string) {
    return this.research.timeline(id);
  }

  @Post("/v1/workspace/runs/:id/pin")
  pin(@Param("id") id: string, @Body() body: { pinned?: boolean }) {
    return this.research.pin(id, body.pinned !== false);
  }

  @Get("/v1/corpus")
  corpus() {
    return this.research.corpus();
  }

  @Post("/v1/corpus/refresh")
  refresh() {
    return this.research.refreshCorpus();
  }

  @Get("/v1/knowledge")
  knowledge() {
    return this.research.knowledgeStats();
  }

  @Get("/v1/knowledge/match")
  knowledgeMatch(@Query("query") query: string) {
    return this.research.knowledgeMatch(query || "");
  }

  @Post("/v1/scenarios/serving")
  serving(@Body() body: Record<string, unknown>) {
    return this.research.servingScenario(body);
  }

  @Post("/v1/scenarios/rag")
  rag(@Body() body: Record<string, unknown>) {
    return this.research.ragScenario(body);
  }
}
