import "reflect-metadata";
import { NestFactory } from "@nestjs/core";
import { ValidationPipe } from "@nestjs/common";
import { AppModule } from "./app.module";

async function bootstrap() {
  const production = (process.env.NODE_ENV || "").toLowerCase() === "production";
  const allowDevAuth = (process.env.ALLOW_DEV_AUTH || "").toLowerCase() === "true";
  const authMode = (process.env.AUTH_MODE || (production && !allowDevAuth ? "clerk" : "dev")).toLowerCase();
  if (production && !allowDevAuth && (authMode === "disabled" || authMode === "dev")) {
    throw new Error(
      `AUTH_MODE=${authMode} is not allowed in production (set ALLOW_DEV_AUTH=true for local Docker)`,
    );
  }
  if (authMode === "clerk" && !process.env.CLERK_SECRET_KEY?.trim()) {
    throw new Error("CLERK_SECRET_KEY is required when AUTH_MODE=clerk");
  }
  if (production) {
    const agentKey = (process.env.API_TO_AGENT_KEY || process.env.AGENT_SHARED_KEY || "").trim();
    if (!agentKey) {
      console.error(
        "kiln-api: AGENT_SHARED_KEY is unset — agent calls will return 401 invalid agent credentials",
      );
    } else if (
      process.env.API_TO_AGENT_KEY?.trim() &&
      process.env.AGENT_SHARED_KEY?.trim() &&
      process.env.API_TO_AGENT_KEY.trim() !== process.env.AGENT_SHARED_KEY.trim()
    ) {
      console.warn(
        "kiln-api: API_TO_AGENT_KEY overrides AGENT_SHARED_KEY and the values differ — agent auth will fail",
      );
    }
  }
  process.env.AUTH_MODE = authMode;

  const app = await NestFactory.create(AppModule, { rawBody: true });
  app.useGlobalPipes(new ValidationPipe({ whitelist: true, transform: true }));
  const origins = (process.env.CORS_ORIGINS || "http://localhost:5173")
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean);
  app.enableCors({
    origin: origins,
    methods: ["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allowedHeaders: [
      "Content-Type",
      "Authorization",
      "X-API-Key",
      "X-Org-Id",
      "X-Correlation-Id",
      "X-Dev-User-Id",
      "X-Dev-Org-Id",
      "X-Dev-Role",
      "Last-Event-Id",
    ],
  });
  await app.listen(Number(process.env.PORT || process.env.API_PORT || 3000), "0.0.0.0");
}

bootstrap();
