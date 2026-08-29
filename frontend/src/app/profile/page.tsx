import { ApiErrorState } from "@/components/api-error-state";
import { Badge, DemoBadge } from "@/components/badges";
import {
  Card,
  DefinitionRow,
  Page,
  PageHeader,
  Section,
} from "@/components/primitives";
import { ProfileEditor } from "@/components/profile-editor";
import { EmptyState, Note } from "@/components/states";
import { getProfileOrNull } from "@/lib/api";
import { formatInt, isDemo, titleCase } from "@/lib/format";
import type { FounderProfile } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * The founder profile, with the eligibility fields editable inline.
 *
 * These are the only facts the deterministic filter reads, which is why
 * this page exists at all.
 */
export default async function ProfilePage() {
  let profile: FounderProfile | null = null;
  let error: unknown = null;
  try {
    profile = await getProfileOrNull();
  } catch (caught) {
    error = caught;
  }

  if (error) {
    return (
      <Page>
        <PageHeader eyebrow="Profile" title="Your profile could not be loaded" />
        <ApiErrorState error={error} what="your profile" />
      </Page>
    );
  }

  if (!profile) {
    return (
      <Page>
        <PageHeader eyebrow="Profile" title="No profile on file" />
        <EmptyState title="The backend has no profile for this founder">
          Kairos seeds a demo profile from{" "}
          <code className="font-mono text-xs">data/demo_founder.json</code> on
          startup. If nothing is here, check that{" "}
          <code className="font-mono text-xs">KAIROS_FOUNDER_ID</code> matches a
          founder the backend knows about.
        </EmptyState>
      </Page>
    );
  }

  const [floor, ceiling] = profile.funding_range;
  const tractionKeys = Object.keys(profile.traction);

  return (
    <Page>
      <PageHeader
        eyebrow="Profile"
        title="What Kairos knows about you"
        lede={
          <>
            Only the structured fields below reach the deterministic eligibility
            filter. Prose never does. That is what keeps text on a funding page
            from talking the filter out of its answer.
          </>
        }
      />

      <Section
        title="Structured facts"
        description="These are compared directly against each opportunity's stated rules, in Python, with no model involved."
      >
        <Card>
          <dl>
            <DefinitionRow term="Founder">
              <span className="font-mono text-sm">{profile.founder_id}</span>
            </DefinitionRow>
            <DefinitionRow term="Institution">{profile.institution}</DefinitionRow>
            <DefinitionRow term="Degree level">
              {titleCase(profile.degree_level)}
            </DefinitionRow>
            <DefinitionRow term="Citizenship">
              <span className="font-mono text-sm">{profile.citizenship}</span>
            </DefinitionRow>
            <DefinitionRow term="Entity">
              {profile.entity_type === "none"
                ? "No legal entity formed"
                : profile.entity_type.replace(/_/g, " ").toUpperCase()}
            </DefinitionRow>
            <DefinitionRow term="Stage">{titleCase(profile.stage)}</DefinitionRow>
            <DefinitionRow term="Team size">
              {formatInt(profile.team_size)}
            </DefinitionRow>
            <DefinitionRow term="Funding range">
              ${formatInt(floor)} – ${formatInt(ceiling)}
            </DefinitionRow>
            <DefinitionRow term="Equity">
              {profile.equity_ok ? (
                <Badge tone="neutral">Open to equity</Badge>
              ) : (
                <Badge tone="info">Non-dilutive only</Badge>
              )}
            </DefinitionRow>
            <DefinitionRow term="Faculty advisor">
              {profile.has_faculty_advisor ? (
                <Badge tone="ok">Yes</Badge>
              ) : (
                <Badge tone="warn">Not yet; some funders require one</Badge>
              )}
            </DefinitionRow>
            <DefinitionRow term="Time you will spend">
              Up to {formatInt(profile.max_application_hours)} hours per
              application
            </DefinitionRow>
            <DefinitionRow term="Geographies">
              {profile.geographies.length > 0 ? (
                <span className="flex flex-wrap gap-1.5">
                  {profile.geographies.map((geo) => (
                    <Badge key={geo} tone="neutral">
                      {geo}
                    </Badge>
                  ))}
                </span>
              ) : (
                <span className="text-ink-muted">None recorded</span>
              )}
            </DefinitionRow>
          </dl>
        </Card>
        <div className="mt-4">
          <ProfileEditor profile={profile} />
        </div>
      </Section>

      {tractionKeys.length > 0 ? (
        <Section
          title="Traction"
          description="Numbers only. These are the most damaging thing an agent could invent onto an application, so they are kept structured and never paraphrased."
        >
          <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-rule bg-rule sm:grid-cols-4">
            {tractionKeys.map((key) => (
              <div key={key} className="bg-surface px-4 py-4">
                <dt className="text-[11px] font-medium uppercase tracking-[0.12em] text-ink-muted">
                  {titleCase(key)}
                </dt>
                <dd className="mt-1 font-serif text-2xl tabular-nums text-ink">
                  {formatInt(profile.traction[key])}
                </dd>
              </div>
            ))}
          </dl>
        </Section>
      ) : null}

      <Section
        title="Knowledge base"
        description="The closed world the Drafter is allowed to draw from. If a claim is not backed by one of these, it does not go on an application."
      >
        {profile.knowledge_base.length === 0 ? (
          <EmptyState title="No knowledge chunks yet">
            With an empty knowledge base the Drafter is disabled entirely. A
            sparse profile produces more questions for you, not more invention.
          </EmptyState>
        ) : (
          <ul className="space-y-3">
            {profile.knowledge_base.map((chunk) => (
              <li
                key={chunk.chunk_id}
                className="rounded-lg border border-rule bg-surface p-4"
              >
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[11px] text-ink-muted">
                    {chunk.source}
                  </span>
                  {chunk.confidence < 1 ? (
                    <Badge tone="warn">
                      confidence {chunk.confidence.toFixed(2)}
                    </Badge>
                  ) : null}
                  {isDemo(chunk.text) ? <DemoBadge /> : null}
                </div>
                <p className="text-[15px] leading-relaxed text-ink-soft">
                  {chunk.text}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Note>
        Edits here replace the profile wholesale. The backend deliberately
        accepts no partial update, so the eligibility filter never runs on a
        half-applied one. Traction and the knowledge base are updated on the
        backend, not here: they are evidence, and evidence is not edited from
        a dashboard.
      </Note>
    </Page>
  );
}
