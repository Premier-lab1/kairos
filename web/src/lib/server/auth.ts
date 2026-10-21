import "server-only";
import { connection } from "next/server";

/**
 * The dashboard is public, so there is nothing to check. Every page still calls this first:
 * its data is per-request (live DB), so it must never be prerendered, also not in runtime prefetches.
 */
export async function requireUser(): Promise<null> {
  await connection();
  return null;
}
