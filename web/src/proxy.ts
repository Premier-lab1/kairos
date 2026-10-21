import { NextResponse } from "next/server";

// The dashboard is public: no gate, requests pass straight through.
export function proxy() {
  return NextResponse.next();
}

export const config = {
  matcher: ["/app", "/app/:path*"],
};
