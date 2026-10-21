/** Marks demo data on the landing page. Required on every block that shows example numbers. */
export function DemoTag({ className = "", label = "Demo data · example" }: { className?: string; label?: string }) {
  return (
    <span
      className={`mono-label inline-flex items-center gap-2 rounded-full border border-current/25 px-3 py-1 text-[0.65rem] opacity-80 ${className}`}
    >
      <span className="size-1.5 rounded-full bg-accent" aria-hidden />
      {label}
    </span>
  );
}
