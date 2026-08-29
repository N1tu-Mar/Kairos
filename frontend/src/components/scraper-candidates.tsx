import { Badge, type Tone } from "@/components/badges";
import {
  formatAwardRange,
  formatDate,
  formatRelative,
  titleCase,
} from "@/lib/format";
import type {
  ScraperCandidate,
  ScraperCandidateGroup,
  ScraperCandidateGroups,
  ScraperLaneName,
  ScraperReviewStatus,
} from "@/lib/types";

const LANE_COPY: Record<
  ScraperLaneName,
  { title: string; body: string; empty: string }
> = {
  university: {
    title: "University search",
    body: "Campus grants, student-founder pitch competitions, and university innovation resources.",
    empty: "No university candidates have been written yet.",
  },
  general: {
    title: "General web search",
    body: "Public grants, competitions, fellowships, and other non-campus funding pages.",
    empty: "No general web candidates have been written yet.",
  },
};

const REVIEW_STATUS: Record<
  ScraperReviewStatus,
  { label: string; tone: Tone; title: string }
> = {
  NEEDS_HUMAN_REVIEW: {
    label: "Review needed",
    tone: "warn",
    title: "A scraper found this row, but no human has accepted or rejected it yet.",
  },
  ACCEPTED: {
    label: "Accepted",
    tone: "ok",
    title: "A human accepted this candidate row.",
  },
  REJECTED: {
    label: "Rejected",
    tone: "neutral",
    title: "A human rejected this candidate row.",
  },
};

function ReviewStatusBadge({ status }: { status: ScraperReviewStatus }) {
  const spec = REVIEW_STATUS[status] ?? {
    label: titleCase(status),
    tone: "neutral" as Tone,
    title: status,
  };
  return (
    <Badge tone={spec.tone} title={spec.title}>
      {spec.label}
    </Badge>
  );
}

function CandidateFacts({ candidate }: { candidate: ScraperCandidate }) {
  const award = formatAwardRange(candidate.award_min, candidate.award_max);
  const facts = [
    candidate.organization,
    award,
    candidate.deadline_iso
      ? `Due ${formatDate(candidate.deadline_iso)}`
      : candidate.deadline
        ? `Deadline ${candidate.deadline}`
        : null,
    candidate.unknown_fields.length > 0
      ? `${candidate.unknown_fields.length} unknown`
      : "All scraper fields filled",
  ].filter((fact): fact is string => Boolean(fact));

  return (
    <ul className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-muted">
      {facts.map((fact, index) => (
        <li key={`${fact}-${index}`} className="flex items-center gap-3">
          {index > 0 ? (
            <span aria-hidden="true" className="text-rule-strong">
              -
            </span>
          ) : null}
          <span>{fact}</span>
        </li>
      ))}
    </ul>
  );
}

function firstCaveat(candidate: ScraperCandidate): string | null {
  return (
    candidate.caveats.find(
      (caveat) =>
        !caveat.startsWith("[founder reviews]") &&
        !caveat.startsWith("[operator note]"),
    ) ?? null
  );
}

function CandidateRow({ candidate }: { candidate: ScraperCandidate }) {
  const caveat = firstCaveat(candidate);
  return (
    <li className="px-4 py-4 sm:px-5">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <ReviewStatusBadge status={candidate.review_status} />
        {candidate.award_type ? (
          <Badge tone="neutral">{candidate.award_type}</Badge>
        ) : null}
      </div>

      <h3 className="font-serif text-lg leading-snug tracking-tight text-ink">
        {candidate.source_url ? (
          <a
            href={candidate.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-accent"
          >
            {candidate.title}
          </a>
        ) : (
          candidate.title
        )}
      </h3>

      <CandidateFacts candidate={candidate} />

      {caveat ? (
        <p className="mt-3 text-sm leading-relaxed text-ink-soft">
          {caveat}
        </p>
      ) : null}

      <p className="mt-3 text-xs text-ink-muted">
        Scraped {formatRelative(candidate.scraped_at)}
      </p>
    </li>
  );
}

function LanePanel({
  name,
  group,
}: {
  name: ScraperLaneName;
  group?: ScraperCandidateGroup;
}) {
  const copy = LANE_COPY[name];
  const candidates = group?.candidates ?? [];
  const total = group?.total ?? 0;
  const extra = Math.max(0, total - candidates.length);

  return (
    <section className="overflow-hidden rounded-lg border border-rule bg-surface">
      <div className="border-b border-rule px-4 py-4 sm:px-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="font-serif text-lg tracking-tight text-ink">
              {copy.title}
            </h3>
            <p className="mt-1 text-sm leading-relaxed text-ink-muted">
              {copy.body}
            </p>
          </div>
          <Badge tone={total > 0 ? "info" : "neutral"}>
            {total} {total === 1 ? "row" : "rows"}
          </Badge>
        </div>
        {group?.source_file ? (
          <p className="mt-2 break-all font-mono text-[11px] text-ink-muted">
            {group.source_file}
          </p>
        ) : null}
      </div>

      {candidates.length > 0 ? (
        <>
          <ul className="divide-y divide-rule">
            {candidates.map((candidate) => (
              <CandidateRow key={candidate.scrape_id} candidate={candidate} />
            ))}
          </ul>
          {extra > 0 ? (
            <p className="border-t border-rule px-4 py-3 text-sm text-ink-muted sm:px-5">
              {extra} more {extra === 1 ? "row" : "rows"} in the candidate file.
            </p>
          ) : null}
        </>
      ) : (
        <p className="px-4 py-8 text-sm leading-relaxed text-ink-muted sm:px-5">
          {copy.empty}
        </p>
      )}
    </section>
  );
}

export function ScraperCandidates({
  groups,
}: {
  groups: ScraperCandidateGroups;
}) {
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <LanePanel name="university" group={groups.university} />
      <LanePanel name="general" group={groups.general} />
    </div>
  );
}
