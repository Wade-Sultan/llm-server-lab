import { NextResponse } from 'next/server';

// Liveness. No DB call on purpose — a Cloud SQL blip should not get an
// otherwise-healthy pod restarted. force-dynamic keeps Next from prerendering
// this at build time and serving a stale static 200.
export const dynamic = 'force-dynamic';

export function GET() {
  return NextResponse.json({ status: 'ok' });
}
