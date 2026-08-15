import "reflect-metadata";
import { NestFactory } from "@nestjs/core";
import { ValidationPipe } from "@nestjs/common";
import { AppModule } from "./app.module";

async function bootstrap() {
  const production = (process.env.NODE_ENV || "").toLowerCase() === "production";
  const authMode = (process.env.AUTH_MODE || (production ? "key" : "disabled")).toLowerCase();
  if (production && authMode === "disabled") {
    throw new Error("AUTH_MODE=disabled is not allowed in production");
  }
  if (authMode !== "disabled" && !process.env.API_SHARED_KEY?.trim()) {
    throw new Error("API_SHARED_KEY is required unless AUTH_MODE=disabled");
  }
  process.env.AUTH_MODE = authMode;
  const app = await NestFactory.create(AppModule);
  app.useGlobalPipes(new ValidationPipe({ whitelist: true, transform: true }));
  const origins = (process.env.CORS_ORIGINS || "http://localhost:5173")
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean);
  app.enableCors({
    origin: origins,
    methods: ["GET", "POST", "OPTIONS"],
    allowedHeaders: ["Content-Type", "Authorization", "X-API-Key"],
  });
  await app.listen(Number(process.env.API_PORT || 3000), "0.0.0.0");
}

bootstrap();
