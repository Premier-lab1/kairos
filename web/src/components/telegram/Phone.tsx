"use client";

import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "@/lib/useReducedMotion";
import { DemoTag } from "@/components/ui/DemoTag";
import { demoSignal, outcomeColor, outcomeLabel, shillerById, tokenById } from "@/lib/demo";
import styles from "./Phone.module.css";

export function Phone() {
  const ref = useRef<HTMLDivElement>(null);
  const [explanation, setExplanation] = useState(false);
  const [muted, setMuted] = useState(false);
  const [started, setStarted] = useState(false);
  const [shown, setShown] = useState(false);
  const reduced = useReducedMotion();
  const muteTarget = shillerById(demoSignal.inside[0].shillerId);

  // Typing indicator → message, once the phone scrolls into view.
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setStarted(true);
        observer.disconnect();
      }
    }, { threshold: 0.25 });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!started) return;
    const timer = window.setTimeout(() => setShown(true), 1100);
    return () => window.clearTimeout(timer);
  }, [started]);

  const messageVisible = reduced || shown;

  const filled = Math.round(demoSignal.confidence / 10);
  const control = "min-h-10 rounded-md border border-line bg-ink-3 px-2 py-2 text-[11px] transition-colors hover:border-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

  return (
    <div className="mx-auto w-full min-w-0 max-w-[320px]">
      <DemoTag className="mb-5 text-muted" />
      <div ref={ref} className={`rounded-[36px] border border-fg/20 bg-ink-2 p-2`}>
        <div className="overflow-hidden rounded-[28px] border border-line bg-ink">
          <div aria-hidden="true" className="mx-auto my-3 h-1 w-12 rounded-full bg-line" />
          <header className="flex items-center gap-3 border-b border-line px-4 pb-4">
            <span aria-hidden="true" className="grid size-10 place-items-center rounded-full bg-accent text-xl font-extrabold text-ink">K</span>
            <div><p className="font-semibold">Kairos</p><p className="font-mono text-[10px] text-muted">bot · sample signal</p></div>
          </header>
          <div className="relative p-3">
            <div aria-hidden="true" className={`absolute left-6 top-6 flex items-center gap-1.5 font-mono text-xs text-muted transition-opacity duration-200 ${messageVisible ? "opacity-0" : "opacity-100"}`}>
              <span>typing</span>{[0, 1, 2].map((dot) => <span key={dot} className={styles.dot} style={{ animationDelay: `${dot * 150}ms` }} />)}
            </div>
            <div className={`transition-all duration-500 ease-out ${messageVisible ? "translate-y-0 opacity-100" : "invisible translate-y-2 opacity-0"}`}>
              <article aria-label="Kairos demo signal" className="rounded-lg border border-line bg-ink-2 p-3 text-xs leading-relaxed">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h3 className="font-mono text-lg font-semibold text-accent">{demoSignal.ticker}</h3>
                  <span className="font-mono text-[10px] text-muted">minute {demoSignal.ageMinutes}</span>
                </div>
                <p className="mt-1 break-all font-mono text-[11px] text-muted">CA: {demoSignal.ca}</p>
                <h4 className="mb-2 mt-4 font-medium">Who is already in</h4>
                <ul className="space-y-2 font-mono text-[10px]">
                  {demoSignal.inside.map(({ shillerId, minute }) => {
                    const shiller = shillerById(shillerId);
                    return <li key={shillerId}><span className="block">{shiller.handle}</span><span className="text-muted">min {minute} · {Math.round(shiller.hitRate * 100)}% win rate</span></li>;
                  })}
                </ul>
                <h4 className="mb-2 mt-4 font-medium">Similar past cases</h4>
                <ul className="space-y-1 font-mono text-[10px]">
                  {demoSignal.similar.slice(0, 2).map(({ tokenId, similarity }) => {
                    const token = tokenById(tokenId);
                    return <li key={tokenId} className="flex flex-wrap justify-between gap-x-2"><span>{token.ticker}</span><span style={{ color: outcomeColor[token.outcome] }}>{outcomeLabel[token.outcome]} · {Math.round(similarity * 100)}%</span></li>;
                  })}
                </ul>
                <div className="mt-4 border-t border-line pt-3">
                  <p className="text-muted">Signal confidence</p>
                  <p className="mt-1 font-mono text-accent"><span aria-hidden="true">{"▰".repeat(filled)}{"▱".repeat(10 - filled)}</span> <span>{demoSignal.confidence}/100</span></p>
                </div>
                <p className="mt-3 text-[10px] text-muted">Not financial advice</p>
              </article>
              <div className="mt-2 grid grid-cols-2 gap-1.5">
                <button type="button" className={control} aria-expanded={explanation} aria-controls="telegram-explanation" onClick={() => setExplanation(!explanation)}>Why?</button>
                <a href="#signal" className={`${control} flex items-center justify-center text-center`}>Open card</a>
                <button type="button" className={`${control} col-span-2 font-mono`} aria-pressed={muted} onClick={() => setMuted(!muted)}>{muted ? `Unmute ${muteTarget.handle}` : `Mute ${muteTarget.handle}`}</button>
              </div>
              <p role="status" className="mt-2 min-h-4 font-mono text-[10px] text-muted">{muted ? `${muteTarget.handle} muted (demo)` : ""}</p>
              <div id="telegram-explanation" hidden={!explanation} className="mt-2 rounded-lg border border-line bg-ink-2 p-3 text-xs leading-relaxed">
                <p className="mb-2 font-medium">Why this signal</p>
                <p className="text-muted">The early CA mentions come from the same shillers as in similar past cases. That is a reason to look at the token, not a price prediction.</p>
                <ul className="mt-2 space-y-2 text-muted">{demoSignal.risks.map((risk) => <li key={risk}>{risk}</li>)}</ul>
              </div>
            </div>
          </div>
          <div aria-hidden="true" className="mx-auto mb-3 h-1 w-20 rounded-full bg-fg/30" />
        </div>
      </div>
    </div>
  );
}
