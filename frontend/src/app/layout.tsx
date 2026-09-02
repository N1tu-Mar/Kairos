import type { Metadata } from "next";
import { Bricolage_Grotesque, Instrument_Sans } from "next/font/google";

import "./globals.css";

/**
 * Two faces, self-hosted by `next/font` so they load from this origin and
 * satisfy the `font-src 'self'` line in the CSP.
 *
 * Bricolage carries the display sizes; its width axis is what keeps a long
 * headline from turning into three limp lines. Instrument Sans does every
 * running word. Numbers, dates and money use the system monospace stack
 * already declared in `globals.css` — tabular figures are the one place a
 * third face would have earned its download, and it does not.
 */
const display = Bricolage_Grotesque({
  subsets: ["latin"],
  variable: "--kairos-display",
  display: "swap",
  axes: ["opsz", "wdth"],
});

const body = Instrument_Sans({
  subsets: ["latin"],
  variable: "--kairos-body",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Kairos",
  description:
    "Non-dilutive funding a student founder is actually eligible for. Watched for, judged, and mostly drafted.",
};

/**
 * The document shell.
 *
 * Deliberately thin: fonts, global styles, the skip link. Chrome belongs to
 * whichever section you are in — the dashboard's navigation lives in
 * `(dashboard)/layout.tsx`, and the landing page and the sign-in screen
 * carry their own. A shared shell here is what made the marketing page
 * inherit a signed-in navigation bar it had no business showing.
 */
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable}`}>
      <body className="min-h-dvh bg-paper text-ink antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-surface focus:px-4 focus:py-2 focus:text-sm focus:shadow"
        >
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
