import { Module } from "@nestjs/common";
import { BullModule } from "@nestjs/bullmq";
import { ResearchController } from "./research.controller";
import { ResearchService } from "./research.service";
import { ResearchProcessor } from "./research.processor";
import { Pool } from "pg";
import { AgentExecutionClient } from "./agent-execution.client";
import { ResearchDispatcher } from "./research.dispatcher";
import { DATABASE_POOL, ResearchRepository } from "./research.repository";
import { AuthService } from "../auth/auth.service";
import { AuthGuard, OrgAdminGuard } from "../auth/auth.guard";
import { AuthWebhookController } from "../auth/auth.webhook.controller";
import { BillingService } from "../billing/billing.service";
import { BillingWebhookController } from "../billing/billing.webhook.controller";

@Module({
  imports: [BullModule.registerQueue({ name: "research" })],
  controllers: [ResearchController, AuthWebhookController, BillingWebhookController],
  providers: [
    {
      provide: DATABASE_POOL,
      useFactory: () =>
        new Pool({
          connectionString:
            process.env.DATABASE_URL ||
            "postgresql://kiln:kiln_dev_password@localhost:5432/kiln",
        }),
    },
    ResearchRepository,
    AgentExecutionClient,
    ResearchDispatcher,
    AuthService,
    AuthGuard,
    OrgAdminGuard,
    BillingService,
    ResearchService,
    ResearchProcessor,
  ],
  exports: [DATABASE_POOL, AuthService, BillingService],
})
export class ResearchModule {}
