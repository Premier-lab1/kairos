"use client";

import { useState } from "react";

type AvatarProps = {
  username: string;
  size: number;
  remote?: boolean;
  /** stored profile image URL (kols.avatar_url, X image hosts only); falls back to unavatar.io, then initials */
  src?: string | null;
  ring?: string;
  className?: string;
};

const palette = ["#405b64", "#675476", "#416456", "#626c82", "#73646c", "#49537a"];

export function Avatar({ username, size, remote = true, src, ring, className = "" }: AvatarProps) {
  const handle = username.replace(/^@/, "");
  const [failed, setFailed] = useState<string[]>([]);
  const candidates = [src, `https://unavatar.io/x/${encodeURIComponent(handle)}?fallback=false`].filter((u): u is string => !!u && /^https:\/\/(pbs\.twimg\.com|abs\.twimg\.com|unavatar\.io)\//.test(u));
  const image = candidates.find((u) => !failed.includes(u));
  let hash = 2166136261;
  for (const char of handle.toLowerCase()) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619) >>> 0;
  const first = hash % palette.length;
  const second = (first + 1 + ((hash >>> 8) % (palette.length - 1))) % palette.length;
  const initials = handle.split(/[_\W]+/).filter(Boolean).map((part) => part[0]).slice(0, 2).join("").toUpperCase();

  return (
    <span className={`relative inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full font-mono ${className}`}
      style={{ width: size, height: size, fontSize: size * 0.34, background: `linear-gradient(135deg, ${palette[first]}, ${palette[second]})`, boxShadow: ring ? `0 0 0 2px ${ring}` : undefined }}
      role="img" aria-label={`@${handle}`}>
      <span aria-hidden="true" className="text-white/90">{initials || "?"}</span>
      {remote && image && (
        // Remote avatars deliberately use a plain image: arbitrary hosts and local fallback.
        // eslint-disable-next-line @next/next/no-img-element
        <img key={image} src={image} alt="" referrerPolicy="no-referrer" loading="lazy"
          className="absolute inset-0 size-full object-cover" onError={() => setFailed((f) => [...f, image])} />
      )}
    </span>
  );
}
