"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { Badge } from "@/components/badges";
import { formatDate } from "@/lib/format";
import type {
  EligibilityAnswerValue,
  EligibilityQuestion,
} from "@/lib/types";

const ANSWERS: { value: EligibilityAnswerValue; label: string }[] = [
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
  { value: "not_sure", label: "Not sure" },
];

export function EligibilityQuestionCard({
  question: initial,
  compact = false,
}: {
  question: EligibilityQuestion;
  compact?: boolean;
}) {
  const router = useRouter();
  const [question, setQuestion] = useState(initial);
  const [saving, setSaving] = useState<EligibilityAnswerValue | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function answer(value: EligibilityAnswerValue) {
    if (saving) return;
    setSaving(value);
    setError(null);
    try {
      const response = await fetch(
        `/api/eligibility-questions/${encodeURIComponent(question.question_id)}/answer`,
        {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ answer: value }),
        },
      );
      const body = (await response.json().catch(() => null)) as
        | EligibilityQuestion
        | { error?: string }
        | null;
      if (!response.ok || !body || !("question_id" in body)) {
        setError(
          body && "error" in body && body.error
            ? body.error
            : `The backend returned ${response.status}.`,
        );
        return;
      }
      setQuestion(body);
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "The answer could not be saved.",
      );
    } finally {
      setSaving(null);
    }
  }

  return (
    <article className={`rounded-lg border border-rule bg-surface ${compact ? "p-4" : "p-5 sm:p-6"}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Badge tone={question.status === "answered" ? "ok" : "warn"}>
              {question.status === "answered" ? "Answered" : "Needs you"}
            </Badge>
            <span className="font-mono text-[11px] text-ink-muted">
              {question.check.replace(/_/g, " ")}
            </span>
          </div>
          <h3 className="font-serif text-lg leading-snug text-ink">
            {question.opportunity_title}
          </h3>
        </div>
        {question.deadline ? (
          <span className="shrink-0 text-xs text-ink-muted">
            Due {formatDate(question.deadline)}
          </span>
        ) : null}
      </div>

      <p className="mt-4 text-[15px] font-medium leading-relaxed text-ink">
        {question.question}
      </p>
      {!compact ? (
        <p className="mt-2 border-l-2 border-rule-strong pl-3 text-sm leading-relaxed text-ink-muted">
          {question.requirement}
        </p>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <div
          role="group"
          aria-label={`Answer ${question.question}`}
          className="inline-flex overflow-hidden rounded-md border border-rule-strong"
        >
          {ANSWERS.map((option, index) => {
            const selected = question.answer === option.value;
            return (
              <button
                key={option.value}
                type="button"
                disabled={saving !== null}
                aria-pressed={selected}
                onClick={() => void answer(option.value)}
                className={`min-w-20 px-3 py-2 text-sm transition-colors disabled:cursor-wait disabled:opacity-60 ${
                  index > 0 ? "border-l border-rule-strong" : ""
                } ${selected ? "bg-accent-soft font-medium text-accent" : "bg-surface text-ink-soft hover:bg-sunk"}`}
              >
                {saving === option.value ? "Saving..." : option.label}
              </button>
            );
          })}
        </div>
        <a
          href={question.source_url}
          target="_blank"
          rel="noreferrer"
          className="text-sm text-ink-muted underline underline-offset-4 hover:text-ink"
        >
          Open source
        </a>
      </div>

      {error ? (
        <p role="alert" className="mt-3 text-sm text-alert">
          {error}
        </p>
      ) : null}
    </article>
  );
}
