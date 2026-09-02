"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/briefing", label: "Briefing" },
  { href: "/inbox", label: "Inbox" },
  { href: "/drafts", label: "Drafts" },
  { href: "/runs", label: "Runs" },
  { href: "/profile", label: "Profile" },
];

/**
 * Whether a nav href matches the current path.
 *
 * Prefix match, so a detail page keeps its section highlighted. Every link
 * here names a section rather than the site root, so there is no root case
 * to special-case — `/` is the landing page and has no nav entry.
 */
function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * The top navigation. Marks the section the current path belongs to.
 */
export function SiteNav() {
  const pathname = usePathname() ?? "/";
  return (
    <nav aria-label="Primary" className="flex items-center gap-1">
      {LINKS.map((link) => {
        const active = isActive(pathname, link.href);
        return (
          <Link
            key={link.href}
            href={link.href}
            aria-current={active ? "page" : undefined}
            className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
              active
                ? "bg-accent-soft font-medium text-accent"
                : "text-ink-muted hover:bg-sunk hover:text-ink"
            }`}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
