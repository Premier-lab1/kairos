"use client";

import { useEffect, useState } from "react";

/** Copies `text` (the full mint) to the clipboard; the only client JS in the token header. */
export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => { if (!copied) return; const id = setTimeout(() => setCopied(false), 1600); return () => clearTimeout(id); }, [copied]);
  return <button type="button" onClick={() => navigator.clipboard?.writeText(text).then(() => setCopied(true)).catch(() => setCopied(false))} aria-live="polite"
    className={`inline-flex items-center rounded border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider transition-colors hover:border-accent hover:text-accent focus-visible:outline-2 focus-visible:outline-accent ${copied ? "border-win text-win" : "border-line text-muted"}`}>{copied ? "Copied" : label}</button>;
}
