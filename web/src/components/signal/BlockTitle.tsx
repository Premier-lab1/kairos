import type { ReactNode } from "react";

export function BlockTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <h3 className="mono-label flex items-center gap-2 text-muted">
        <span className="size-1.5 bg-accent" aria-hidden />
        {children}
      </h3>
      {aside ? <span className="text-right font-mono text-[10px] text-muted">{aside}</span> : null}
    </div>
  );
}
