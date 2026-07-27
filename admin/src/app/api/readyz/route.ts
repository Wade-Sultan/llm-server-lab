import { NextResponse } from 'next/server';

import { db } from '@/lib/prisma';

// Readiness: admin is entirely Prisma-backed, so an instance that cannot reach
// Postgres has nothing useful to serve and should drop out of the Service's
// endpoints rather than serve errors during a rollout.
export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    await db.$queryRaw`SELECT 1`;
    return NextResponse.json({ status: 'ready' });
  } catch (error) {
    console.error('readiness check failed', error);
    return NextResponse.json(
      { status: 'not_ready', reason: 'database' },
      { status: 503 },
    );
  }
}
