import { MiddlewareConsumer, Module, NestModule, RequestMethod } from "@nestjs/common";
import { BullModule } from "@nestjs/bullmq";
import { ResearchModule } from "./research/research.module";
import { ApiKeyMiddleware } from "./api-key.middleware";
import { ResearchController } from "./research/research.controller";

@Module({
  imports: [
    BullModule.forRoot({
      connection: {
        url: process.env.REDIS_URL || "redis://localhost:6379/0",
      },
    }),
    ResearchModule,
  ],
})
export class AppModule implements NestModule {
  configure(consumer: MiddlewareConsumer) {
    consumer
      .apply(ApiKeyMiddleware)
      .exclude({ path: "health", method: RequestMethod.GET })
      .forRoutes(ResearchController);
  }
}
