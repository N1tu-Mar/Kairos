import type { Metadata } from "next";
import Link from "next/link";

import { Horizon } from "@/components/landing/horizon";
import { Reveal } from "@/components/landing/reveal";

export const metadata: Metadata = {
  title: "Kairos — funding you can still catch",
  description:
    "Campus grants, fellowships and cash prizes for student founders. Kairos watches for them, checks whether you qualify, and drafts what it can.",
};

/** The four stages of the loop, in the order they run. */
const STAGES = [
  {
    name: "Watches",
    body: "Grants.gov, a hand-checked catalog, and your campus's own pages. On a schedule, not when you remember.",
  },
  {
    name: "Judges",
    body: "Eligibility rules run first, in plain code. If a rule says you do not qualify, no model gets to argue with it.",
  },
  {
    name: "Drafts",
    body: "Answers built from what you already told it, in your words, with the source of each one recorded.",
  },
  {
    name: "Asks",
    body: "It interrupts you for the handful of things it genuinely cannot answer. Nothing else reaches you.",
  },
];

/** The limits, stated as limits. */
const LIMITS = [
  {
    label: "Never submits",
    body: "Kairos prepares an application and stops. Submitting is a decision, and you make it.",
  },
  {
    label: "Never invents",
    body: "Every drafted sentence traces back to something you wrote. A fact it does not have is a question it asks.",
  },
  {
    label: "Never pads",
    body: "59 programmes in the catalog. 52 of them verified line by line against their live pages.",
  },
];

/**
 * The landing page.
 *
 * Public: the middleware lets `/` through unauthenticated, and nothing here
 * reads founder data or touches the API proxy. Everything below the fold is
 * static markup — the only client code on the page is the scroll reveal.
 */
export default function LandingPage() {
  return (
    <div className="night min-h-dvh">
      <div className="mx-auto w-full max-w-[64rem] px-6 sm:px-10">
        <header className="flex items-center justify-between py-7">
          <span className="font-display text-[1.0625rem] font-semibold tracking-[-0.02em]">
            Kairos
          </span>
          <Link
            href="/login"
            className="t-dim text-sm transition-colors hover:[color:var(--chalk)]"
          >
            Sign in
          </Link>
        </header>

        <main id="main">
          {/* --- Hero. The horizon is the argument; the words introduce it. --- */}
          <section className="pb-16 pt-10 sm:pb-24 sm:pt-16">
            <h1
              className="rise font-display text-[2.75rem] font-medium leading-[0.94] tracking-[-0.035em] sm:text-[3.75rem] lg:text-[4.5rem]"
              style={{ "--step": 0 } as React.CSSProperties}
            >
              The money has
              <br />a date on it.
            </h1>

            <p
              className="rise t-dim mt-7 max-w-[38rem] text-[1.0625rem] leading-[1.65]"
              style={{ "--step": 1 } as React.CSSProperties}
            >
              Campus grants, fellowships and cash prizes for student founders.
              Nothing lists them all, and nothing tells you when they close.
              Kairos watches, checks whether you actually qualify, and drafts
              what it can before it wakes you.
            </p>

            <div
              className="rise mt-9"
              style={{ "--step": 2 } as React.CSSProperties}
            >
              <Link
                href="/login"
                className="inline-flex items-center rounded-full px-6 py-3 text-[0.9375rem] font-medium transition-transform duration-200 hover:-translate-y-0.5"
                style={{
                  background: "var(--brass)",
                  color: "var(--night)",
                }}
              >
                Start with your profile
              </Link>
            </div>

            <div className="mt-14 sm:mt-20">
              <Horizon />
            </div>
          </section>

          {/* --- The loop. Four stages on one continuing rule: the order is
                 the information, so the rule carries it and no numeral is
                 needed. --- */}
          <Reveal>
            <section className="hairline border-t pt-14 pb-20 sm:pb-28">
              <h2 className="font-display max-w-[26rem] text-[1.75rem] font-medium leading-[1.15] tracking-[-0.025em] sm:text-[2.125rem]">
                The loop runs while you are asleep.
              </h2>

              <ol className="mt-12 grid gap-10 sm:grid-cols-2 lg:grid-cols-4 lg:gap-7">
                {STAGES.map((stage) => (
                  <li
                    key={stage.name}
                    className="hairline relative border-t pt-6"
                  >
                    <span className="stage-dot absolute -top-[3px] left-0 block h-[7px] w-[7px] rounded-full" />
                    <h3 className="t-brass font-mono text-[0.6875rem] uppercase tracking-[0.18em]">
                      {stage.name}
                    </h3>
                    <p className="t-dim mt-3 text-[0.9375rem] leading-[1.6]">
                      {stage.body}
                    </p>
                  </li>
                ))}
              </ol>
            </section>
          </Reveal>

          {/* --- The limits. No rules and no dots here, so it reads as a
                 different kind of claim from the loop above. --- */}
          <Reveal>
            <section className="hairline border-t pt-14 pb-20 sm:pb-28">
              <h2 className="font-display text-[1.75rem] font-medium leading-[1.15] tracking-[-0.025em] sm:text-[2.125rem]">
                What it will not do.
              </h2>

              <dl className="mt-12 grid gap-10 sm:grid-cols-3 sm:gap-8">
                {LIMITS.map((limit) => (
                  <div key={limit.label}>
                    <dt className="font-display text-[1.125rem] font-medium tracking-[-0.015em]">
                      {limit.label}
                    </dt>
                    <dd className="t-dim mt-2.5 text-[0.9375rem] leading-[1.6]">
                      {limit.body}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          </Reveal>

          {/* --- Close. --- */}
          <Reveal>
            <section className="hairline border-t py-16 sm:py-20">
              <div className="flex flex-col gap-8 sm:flex-row sm:items-end sm:justify-between">
                <h2 className="font-display max-w-[22rem] text-[1.75rem] font-medium leading-[1.15] tracking-[-0.025em] sm:text-[2.125rem]">
                  Tell it about your startup once.
                </h2>
                <Link
                  href="/login"
                  className="inline-flex shrink-0 items-center self-start rounded-full px-6 py-3 text-[0.9375rem] font-medium transition-transform duration-200 hover:-translate-y-0.5 sm:self-auto"
                  style={{ background: "var(--brass)", color: "var(--night)" }}
                >
                  Start with your profile
                </Link>
              </div>
            </section>
          </Reveal>
        </main>

        <footer className="hairline t-dim border-t py-10 text-xs leading-relaxed">
          <p className="max-w-[34rem]">
            <span className="t-chalk">καιρός</span> — the window that opens and
            shuts, as against <span className="t-chalk">chronos</span>, the
            clock time that merely elapses. Lysippos gave him winged feet and a
            forelock in front, bald behind. A funding deadline behaves the same
            way.
          </p>
        </footer>
      </div>
    </div>
  );
}
