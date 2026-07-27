import { PrismaClient } from '@prisma/client';

const globalForPrisma = global as unknown as {
  prisma: PrismaClient;
  prismaShutdownHooked: boolean;
};

export const db = globalForPrisma.prisma ?? new PrismaClient();

if (process.env.NODE_ENV !== 'production') globalForPrisma.prisma = db;

// Close the pool when the pod is drained. Next's standalone server handles
// SIGTERM itself, so this only disconnects — calling process.exit() here would
// cut its own graceful shutdown short. Guarded because this module can be
// evaluated more than once per process.
if (!globalForPrisma.prismaShutdownHooked) {
  globalForPrisma.prismaShutdownHooked = true;
  for (const signal of ['SIGTERM', 'SIGINT'] as const) {
    process.once(signal, () => {
      void db.$disconnect();
    });
  }
}
