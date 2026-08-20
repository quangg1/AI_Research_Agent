import { MiddlewareConsumer, Module, NestModule } from "@nestjs/common";
import { BullModule } from "@nestjs/bullmq";
import { ResearchModule } from "./research/research.module";
import { CorrelationMiddleware } from "./auth/correlation.middleware";

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
    consumer.apply(CorrelationMiddleware).forRoutes("*");
  }
}
