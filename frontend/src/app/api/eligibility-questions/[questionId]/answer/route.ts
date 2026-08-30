import { NextResponse } from "next/server";

import { answerEligibilityQuestion } from "@/lib/api";
import { errorResponse } from "@/lib/errors";
import { ELIGIBILITY_ANSWERS } from "@/lib/types";
import type { EligibilityAnswerValue } from "@/lib/types";

export const dynamic = "force-dynamic";

function isEligibilityAnswer(value: unknown): value is EligibilityAnswerValue {
  return (
    typeof value === "string" &&
    (ELIGIBILITY_ANSWERS as string[]).includes(value)
  );
}

export async function PUT(
  request: Request,
  { params }: { params: Promise<{ questionId: string }> },
) {
  const { questionId } = await params;
  let answer: EligibilityAnswerValue;
  try {
    const body = (await request.json()) as { answer?: unknown };
    if (!isEligibilityAnswer(body.answer)) {
      return NextResponse.json(
        { error: `answer must be one of: ${ELIGIBILITY_ANSWERS.join(", ")}.` },
        { status: 400 },
      );
    }
    answer = body.answer;
  } catch {
    return NextResponse.json(
      { error: "Request body must be JSON." },
      { status: 400 },
    );
  }

  try {
    const updated = await answerEligibilityQuestion(
      decodeURIComponent(questionId),
      answer,
    );
    return NextResponse.json(updated);
  } catch (error) {
    return errorResponse(
      error,
      "PUT /api/eligibility-questions/[questionId]/answer",
      "The eligibility answer could not be saved.",
    );
  }
}
