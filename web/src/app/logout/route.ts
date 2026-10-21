import { NextResponse, type NextRequest } from "next/server";

// No sessions any more: kept so old links and bookmarks still land somewhere sensible.
export function GET(request: NextRequest) {
  return NextResponse.redirect(new URL("/app", request.url), 303);
}

export function POST(request: NextRequest) {
  return NextResponse.redirect(new URL("/app", request.url), 303);
}
