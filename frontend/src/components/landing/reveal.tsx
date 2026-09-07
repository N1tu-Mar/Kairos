"use client";

import { useEffect, useRef, type ReactNode } from "react";

/**
 * Fades a section up as it scrolls into view.
 *
 * The starting state is applied by script rather than by the stylesheet, and
 * only to elements that are genuinely below the fold. That ordering is the
 * whole point: a stylesheet that hides content up front leaves a visitor
 * with no JavaScript — or a failed chunk — staring at an empty page. Here
 * the served HTML is already the finished composition, and motion is added
 * to it afterwards.
 *
 * Reduced motion needs no branch here: `.armed` carries no visual effect
 * outside the `prefers-reduced-motion: no-preference` block in the
 * stylesheet, so the class is set and does nothing.
 */
export function Reveal({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver === "undefined") return;

    // Already on screen: leave it alone rather than hiding it and fading it
    // back in, which reads as a glitch on first paint.
    if (node.getBoundingClientRect().top < window.innerHeight) return;

    node.classList.add("armed");

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          entry.target.classList.add("shown");
          observer.unobserve(entry.target);
        }
      },
      { rootMargin: "0px 0px -12% 0px" },
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={ref} className={`reveal ${className}`}>
      {children}
    </div>
  );
}
