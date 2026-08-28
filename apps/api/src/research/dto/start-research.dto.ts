import { Type } from "class-transformer";
import { IsBoolean, IsOptional, IsString, IsUUID, Length, ValidateNested } from "class-validator";
import { LlmCredentialDto } from "./llm-credential.dto";

export class StartResearchDto {
  @IsString()
  @Length(8, 4000)
  query!: string;

  @IsOptional()
  @IsBoolean()
  fresh?: boolean;

  @IsOptional()
  @IsUUID()
  parentRunId?: string;

  @IsOptional()
  @ValidateNested()
  @Type(() => LlmCredentialDto)
  llm?: LlmCredentialDto;
}

export class DuplicateResearchDto {
  @IsOptional()
  @ValidateNested()
  @Type(() => LlmCredentialDto)
  llm?: LlmCredentialDto;
}
