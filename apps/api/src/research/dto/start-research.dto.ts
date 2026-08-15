import { IsBoolean, IsOptional, IsString, Length } from "class-validator";

export class StartResearchDto {
  @IsString()
  @Length(8, 4000)
  query!: string;

  @IsOptional()
  @IsBoolean()
  fresh?: boolean;
}
