"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export function Refresh() {
  const router = useRouter();
  useEffect(() => {
    const timer = setInterval(() => { if (document.visibilityState === "visible") router.refresh(); }, 60_000);
    return () => clearInterval(timer);
  }, [router]);
  return null;
}
