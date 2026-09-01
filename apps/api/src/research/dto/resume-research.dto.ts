import { Type } from "class-transformer";
import { IsArray, IsObject, IsOptional, IsString, ValidateNested } from "class-validator";
import { LlmCredentialDto } from "./llm-credential.dto";

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

  @IsOptional()
  @IsObject()
  plan?: Record<string, unknown>;

  @IsOptional()
  @ValidateNested()
  @Type(() => LlmCredentialDto)
  llm?: LlmCredentialDto;
}
