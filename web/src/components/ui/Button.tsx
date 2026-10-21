import type { ReactNode } from "react";

type Variant = "primary" | "ghost" | "ghost-light";

const styles: Record<Variant, string> = {
  primary: "bg-accent text-ink hover:bg-[#ff6a3a]",
  ghost: "border border-fg/25 text-fg hover:border-fg/60",
  "ghost-light": "border border-ink/25 text-ink hover:border-ink/60",
};

/** Pill button / link. */
export function Button({
  href,
  children,
  variant = "primary",
  className = "",
}: {
  href: string;
  children: ReactNode;
  variant?: Variant;
  className?: string;
}) {
  return (
    <a
      href={href}
      className={`mono-label inline-flex h-11 items-center justify-center gap-2 rounded-full px-5 text-[0.75rem] font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${styles[variant]} ${className}`}
    >
      {children}
    </a>
  );
}
