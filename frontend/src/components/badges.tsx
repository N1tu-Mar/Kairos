import type { ReactNode } from "react";

import type {
  AuditVerdict,
  DraftStatus,
  FieldStatus,
  InboxKind,
  InboxState,
} from "@/lib/types";

/**
 * One badge component, several vocabularies mapped onto it. Colour carries
 * meaning, so the mapping lives here rather than being retyped per view.
 */

export type Tone = "ok" | "warn" | "alert" | "info" | "neutral" | "accent";

const TONE_CLASS: Record<Tone, string> = {
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  alert: "bg-alert-soft text-alert",
  info: "bg-info-soft text-info",
  neutral: "bg-neutral-soft text-neutral",
  accent: "bg-accent-soft text-accent",
};

export function Badge({
  tone = "neutral",
  children,
  title,
}: {
  tone?: Tone;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.08em] ${TONE_CLASS[tone]}`}
    >
      {children}
    </span>
  );
}

const INBOX_KIND: Record<InboxKind, { label: string; tone: Tone; title: string }> = {
  APPLY: {
    label: "Apply",
    tone: "ok",
    title: "The Assessor judged this worth your time and the escalation policy agreed.",
  },
  MAYBE: {
    label: "Maybe",
    tone: "warn",
    title: "Worth a look, but something is in the way that you could remove.",
  },
  UNKNOWN_HIGH_VALUE: {
    label: "Unknown · high value",
    tone: "info",
    title:
      "Eligibility could not be determined from the source text, and the award is large enough that guessing would be worse than asking.",
  },
  DEADLINE_URGENT: {
    label: "Deadline urgent",
    tone: "alert",
    title: "Surfaced because the deadline is close.",
  },
  COLD_START: {
    label: "Cold start",
    tone: "neutral",
    title:
      "Your knowledge base is still sparse, so drafting was deliberately limited.",
  },
};

export function InboxKindBadge({ kind }: { kind: InboxKind }) {
  const spec = INBOX_KIND[kind] ?? {
    label: kind,
    tone: "neutral" as Tone,
    title: kind,
  };
  return (
    <Badge tone={spec.tone} title={spec.title}>
      {spec.label}
    </Badge>
  );
}

const DRAFT_STATUS: Record<DraftStatus, { label: string; tone: Tone }> = {
  READY: { label: "Ready to review", tone: "ok" },
  BLOCKED: { label: "Blocked", tone: "alert" },
  DRAFT: { label: "In progress", tone: "neutral" },
};

export function DraftStatusBadge({ status }: { status: DraftStatus }) {
  const spec = DRAFT_STATUS[status] ?? { label: status, tone: "neutral" as Tone };
  return <Badge tone={spec.tone}>{spec.label}</Badge>;
}

const FIELD_STATUS: Record<FieldStatus, { label: string; tone: Tone; title: string }> = {
  KNOWN: {
    label: "Known",
    tone: "ok",
    title: "Taken straight from a structured fact you already gave.",
  },
  REUSED: {
    label: "Reused",
    tone: "info",
    title: "Lifted from an answer you gave on an earlier application.",
  },
  GENERATED: {
    label: "Generated",
    tone: "accent",
    title: "Written by the Drafter, and required to carry provenance.",
  },
  NEEDS_FOUNDER: {
    label: "Needs you",
    tone: "alert",
    title: "Kairos would have to invent this, so it did not.",
  },
};

export function FieldStatusBadge({ status }: { status: FieldStatus }) {
  const spec = FIELD_STATUS[status] ?? {
    label: status,
    tone: "neutral" as Tone,
    title: status,
  };
  return (
    <Badge tone={spec.tone} title={spec.title}>
      {spec.label}
    </Badge>
  );
}

const AUDIT: Record<AuditVerdict, { label: string; tone: Tone; title: string }> = {
  SUPPORTED: {
    label: "Audit: supported",
    tone: "ok",
    title: "The Auditor found a quote in your knowledge base backing this answer.",
  },
  UNSUPPORTED: {
    label: "Audit: unsupported",
    tone: "alert",
    title: "The Auditor could not back this answer. The Drafter loses that argument.",
  },
  UNVERIFIABLE: {
    label: "Audit: unverifiable",
    tone: "warn",
    title: "The Auditor could neither confirm nor refute this answer.",
  },
};

export function AuditBadge({ verdict }: { verdict: AuditVerdict }) {
  const spec = AUDIT[verdict] ?? {
    label: verdict,
    tone: "neutral" as Tone,
    title: verdict,
  };
  return (
    <Badge tone={spec.tone} title={spec.title}>
      {spec.label}
    </Badge>
  );
}

const INBOX_STATE: Record<InboxState, { label: string; tone: Tone; title: string } | null> = {
  // "new" gets no badge — it is the default and a badge for it is noise.
  new: null,
  opened: {
    label: "Opened",
    tone: "info",
    title: "You marked this as opened.",
  },
  dismissed: {
    label: "Dismissed",
    tone: "neutral",
    title: "You dismissed this. The run's verdict is unchanged — only your state on it.",
  },
  applied: {
    label: "Applied",
    tone: "ok",
    title: "You marked this as applied. Kairos records that; it never submits anything itself.",
  },
};

/** What the founder did with the item. Renders nothing for "new". */
export function InboxStateBadge({ state }: { state: InboxState }) {
  const spec = INBOX_STATE[state];
  if (!spec) return null;
  return (
    <Badge tone={spec.tone} title={spec.title}>
      {spec.label}
    </Badge>
  );
}

/** Synthetic seed rows carry `[DEMO]`. That marking never gets stripped. */
export function DemoBadge() {
  return (
    <Badge tone="neutral" title="Synthetic record from the demo catalog. Not a real funding opportunity.">
      Demo data
    </Badge>
  );
}

export function PassiveBadge() {
  return (
    <Badge
      tone="neutral"
      title="Overflow past the per-run surfacing cap. Listed, never notified."
    >
      Also found
    </Badge>
  );
}
