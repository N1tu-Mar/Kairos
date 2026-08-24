import type { Metadata } from "next";
import Link from "next/link";

import { SiteNav } from "@/components/site-nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Kairos",
  description:
    "Non-dilutive funding a student founder is actually eligible for — watched for, judged, and mostly drafted.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-dvh bg-paper text-ink antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-surface focus:px-4 focus:py-2 focus:text-sm focus:shadow"
        >
          Skip to content
        </a>

        <div className="border-b border-rule bg-surface/70">
          <div className="mx-auto flex w-full max-w-5xl flex-col gap-3 px-5 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-8">
            <Link href="/" className="flex items-baseline gap-2.5">
              <span className="font-serif text-lg tracking-tight text-ink">
                Kairos
              </span>
              <span className="hidden text-xs text-ink-muted sm:inline">
                the opportune moment
              </span>
            </Link>
            <SiteNav />
          </div>
        </div>

        <main id="main">{children}</main>

        <footer className="border-t border-rule">
          <div className="mx-auto w-full max-w-5xl px-5 py-8 text-xs leading-relaxed text-ink-muted sm:px-8">
            <p>
              Kairos prepares applications. It never submits one — submission
              is a decision, and a person makes it.
            </p>
            <p className="mt-2">
              Rows marked <span className="font-mono">[DEMO]</span> are
              synthetic records from the demo catalog. They are not real
              funding opportunities.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
