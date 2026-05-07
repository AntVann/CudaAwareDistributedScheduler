import type { ReactNode } from "react";

/**
 * Card primitive matching the design system in index.css.
 * - default: padded card body (.card + .card-pad)
 * - pass `bare` to skip padding when the caller manages internal sections
 *   themselves (.card-head, .card-body, .card-foot).
 */
export default function Card({
  children,
  className = "",
  bare = false,
}: {
  children: ReactNode;
  className?: string;
  bare?: boolean;
}) {
  const cls = ["card", bare ? "" : "card-pad", className].filter(Boolean).join(" ");
  return <div className={cls}>{children}</div>;
}
