import { NextResponse } from "next/server";

import { uploadIntakeDocument } from "@/lib/api";
import { errorResponse } from "@/lib/errors";
import {
  intakeFileProblem,
  MAX_INTAKE_UPLOAD_BODY_BYTES,
  validResourceId,
} from "@/lib/intake-route-contracts";

export const dynamic = "force-dynamic";

interface RouteContext {
  params: Promise<{ sessionId: string }>;
}

export async function POST(request: Request, context: RouteContext) {
  const { sessionId } = await context.params;
  if (!validResourceId(sessionId)) {
    return NextResponse.json({ error: "Invalid intake session." }, { status: 400 });
  }
  const declared = Number(request.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > MAX_INTAKE_UPLOAD_BODY_BYTES) {
    return NextResponse.json({ error: "Each file must be 10 MB or smaller." }, { status: 413 });
  }
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return NextResponse.json({ error: "The upload is not valid multipart data." }, { status: 400 });
  }
  const files = form.getAll("file");
  if (files.length !== 1 || !(files[0] instanceof File) || [...form.keys()].some((key) => key !== "file")) {
    return NextResponse.json({ error: "Upload exactly one document." }, { status: 400 });
  }
  const file = files[0];
  const problem = intakeFileProblem(file);
  if (problem) return NextResponse.json({ error: problem }, { status: 400 });
  try {
    return NextResponse.json(await uploadIntakeDocument(sessionId, file));
  } catch (error) {
    return errorResponse(
      error,
      "POST /api/intake/[sessionId]/documents",
      "The document could not be processed.",
      { 409: "Remove a document before adding another.", 413: "Each file must be 10 MB or smaller.", 422: "The document could not be read safely." },
    );
  }
}
