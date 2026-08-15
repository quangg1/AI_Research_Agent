import { IsArray, IsObject, IsOptional, IsString } from "class-validator";

export class ResumeResearchDto {
  @IsOptional()
  @IsString()
  action?: string;

  @IsOptional()
  @IsString()
  notes?: string;

  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  extra_questions?: string[];

  @IsOptional()
  @IsObject()
  brief?: Record<string, unknown>;
}
