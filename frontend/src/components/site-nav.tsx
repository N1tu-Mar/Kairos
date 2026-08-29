"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Briefing" },
  { href: "/inbox", label: "Inbox" },
  { href: "/drafts", label: "Drafts" },
  { href: "/runs", label: "Runs" },
  { href: "/profile", label: "Profile" },
];

/**
 * Whether a nav href matches the current path.
 *
 * Exact match for the root so `/` is not marked active on every page;
 * prefix match elsewhere so a detail page keeps its section highlighted.
 */
function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
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
                ? "bg-accent-soft font-medium text-ink"
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
