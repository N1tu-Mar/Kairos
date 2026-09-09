import type {
  IntakeClaimCategory,
  IntakeClaimUpdate,
  IntakeFactUpdate,
} from "@/lib/types";

export const MAX_INTAKE_FILE_BYTES = 10 * 1024 * 1024;
export const MAX_INTAKE_UPLOAD_BODY_BYTES = MAX_INTAKE_FILE_BYTES + 256 * 1024;

const fields = new Set([
  "startup_description",
  "full_name",
  "degree_level",
  "institution",
  "major",
  "citizenship",
  "entity_type",
  "team_size",
  "stage",
  "traction",
  "funding_range",
  "equity_ok",
  "has_faculty_advisor",
  "max_application_hours",
  "geographies",
]);
const categories = new Set<IntakeClaimCategory>([
  "problem",
  "solution",
  "customers",
  "market",
  "business_model",
  "differentiation",
  "team",
  "traction",
  "milestones",
  "funding_needs",
]);
const mediaByExtension: Record<string, ReadonlySet<string>> = {
  pdf: new Set(["application/pdf"]),
  pptx: new Set([
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ]),
  txt: new Set(["text/plain"]),
  md: new Set(["text/markdown", "text/plain"]),
  markdown: new Set(["text/markdown", "text/plain"]),
};

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function exactKeys(value: Record<string, unknown>, allowed: string[]): boolean {
  const keys = Object.keys(value);
  return keys.every((key) => allowed.includes(key));
}

export function validResourceId(value: string): boolean {
  return value.length > 0 && value.length <= 200;
}

export function isRevisionBody(value: unknown): value is { expected_revision: number } {
  const body = record(value);
  return Boolean(
    body &&
      Object.keys(body).length === 1 &&
      Number.isInteger(body.expected_revision) &&
      Number(body.expected_revision) >= 0,
  );
}

export function isFactUpdate(value: unknown): value is IntakeFactUpdate {
  const body = record(value);
  if (!body || !exactKeys(body, ["action", "expected_revision", "value", "client_action_id"])) {
    return false;
  }
  if (!(["confirm", "correct", "reject"] as unknown[]).includes(body.action)) return false;
  if (!Number.isInteger(body.expected_revision) || Number(body.expected_revision) < 0) return false;
  if (
    body.client_action_id !== undefined &&
    (typeof body.client_action_id !== "string" || !validResourceId(body.client_action_id))
  ) {
    return false;
  }
  return body.action !== "correct" || body.value !== undefined;
}

export function isClaimUpdate(value: unknown): value is IntakeClaimUpdate {
  const body = record(value);
  if (!body || !exactKeys(body, ["action", "expected_revision", "text", "category", "client_action_id"])) {
    return false;
  }
  if (!(["confirm", "correct", "reject"] as unknown[]).includes(body.action)) return false;
  if (!Number.isInteger(body.expected_revision) || Number(body.expected_revision) < 0) return false;
  if (typeof body.client_action_id !== "string" || !validResourceId(body.client_action_id)) return false;
  if (body.text !== undefined && (typeof body.text !== "string" || body.text.length > 4_000)) return false;
  if (body.category !== undefined && !categories.has(body.category as IntakeClaimCategory)) return false;
  return body.action !== "correct" || (typeof body.text === "string" && body.text.trim().length > 0);
}

export function isIntakeField(value: string): boolean {
  return fields.has(value);
}

export function intakeFileProblem(file: File): string | null {
  if (!file.name || file.name.length > 200) return "The file name is invalid.";
  if (file.size === 0) return "The file is empty.";
  if (file.size > MAX_INTAKE_FILE_BYTES) return "Each file must be 10 MB or smaller.";
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  const media = mediaByExtension[extension];
  if (!media) return "Choose a PDF, PPTX, TXT, or Markdown file.";
  const normalized = file.type.split(";", 1)[0].toLowerCase();
  if (!media.has(normalized)) return "The file type does not match its extension.";
  return null;
}
