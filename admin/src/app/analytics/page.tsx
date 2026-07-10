export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { formatDate } from '@/lib/utils';

// Prisma returns Decimal | null for _avg/_sum on numeric columns; normalize to a number.
function num(value: unknown): number {
  return value == null ? 0 : Number(value);
}

// Costs here are tiny (fractions of a cent up to a few cents), so show extra precision.
function fmtCost(value: number | null): string {
  if (value == null) return '—';
  return value.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  });
}

function fmtPct(value: number | null): string {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`;
}

export default async function AnalyticsPage() {
  const [
    convAgg,
    completedAgg,
    buildSessionAgg,
    totalMessages,
    userMessages,
    assistantMessages,
    recent,
  ] = await Promise.all([
    // All conversations: spend, tokens, calls, and average cost per chat.
    db.conversation.aggregate({
      _count: true,
      _avg: { totalCostUsd: true },
      _sum: {
        totalCostUsd: true,
        totalTokensIn: true,
        totalTokensOut: true,
        llmCallCount: true,
      },
    }),
    // Conversations that reached a recommendation = "completed builds".
    db.conversation.aggregate({
      where: { reachedRecommendation: true },
      _count: true,
      _avg: { totalCostUsd: true },
    }),
    // DSPy build sessions (populates once the component pipeline is wired).
    db.buildSession.aggregate({
      _count: true,
      _avg: { totalCostUsd: true, budgetDeltaCents: true },
      _sum: { compatibilityOverrideCount: true },
    }),
    db.message.count(),
    db.message.count({ where: { role: 'user' } }),
    db.message.count({ where: { role: 'assistant' } }),
    db.conversation.findMany({
      orderBy: { createdAt: 'desc' },
      take: 20,
      include: { _count: { select: { messages: true } } },
    }),
  ]);

  const totalConversations = convAgg._count;
  const completedBuilds = completedAgg._count;
  const dspyBuilds = buildSessionAgg._count;

  const totalSpend = num(convAgg._sum.totalCostUsd);
  const avgCostPerChat = totalConversations > 0 ? num(convAgg._avg.totalCostUsd) : null;
  const avgCostPerCompletedBuild = completedBuilds > 0 ? num(completedAgg._avg.totalCostUsd) : null;
  const avgCostPerDspyBuild = dspyBuilds > 0 ? num(buildSessionAgg._avg.totalCostUsd) : null;

  const completionRate =
    totalConversations > 0 ? completedBuilds / totalConversations : null;
  const totalTokensIn = num(convAgg._sum.totalTokensIn);
  const totalTokensOut = num(convAgg._sum.totalTokensOut);
  const totalCalls = num(convAgg._sum.llmCallCount);

  // Headline cost tiles.
  const costTiles = [
    { label: 'Total LLM Spend', value: fmtCost(totalSpend), sub: `${totalCalls.toLocaleString()} LLM calls` },
    { label: 'Avg Cost / Chat', value: fmtCost(avgCostPerChat), sub: `${totalConversations.toLocaleString()} conversations` },
    {
      label: 'Avg Cost / Completed Build',
      value: fmtCost(avgCostPerCompletedBuild),
      sub: `${completedBuilds.toLocaleString()} completed · ${fmtPct(completionRate)} rate`,
    },
    {
      label: 'Avg Cost / DSPy Build',
      value: fmtCost(avgCostPerDspyBuild),
      sub: dspyBuilds > 0 ? `${dspyBuilds.toLocaleString()} build sessions` : 'no data yet',
    },
  ];

  // Volume / token tiles.
  const volumeTiles = [
    { label: 'Total Conversations', value: totalConversations.toLocaleString() },
    { label: 'Completed Builds', value: completedBuilds.toLocaleString() },
    { label: 'Total Messages', value: totalMessages.toLocaleString() },
    { label: 'User / Assistant', value: `${userMessages.toLocaleString()} / ${assistantMessages.toLocaleString()}` },
    { label: 'Tokens In', value: totalTokensIn.toLocaleString() },
    { label: 'Tokens Out', value: totalTokensOut.toLocaleString() },
  ];

  return (
    <div className="p-8 space-y-8">
      <div>
        <h1 className="text-2xl font-bold">LLM Analytics</h1>
        <p className="text-muted-foreground mt-1">
          OpenRouter cost, token usage, and conversation activity
        </p>
      </div>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">Cost</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {costTiles.map((t) => (
            <Card key={t.label}>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">{t.label}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-bold">{t.value}</p>
                <p className="text-xs text-muted-foreground mt-1">{t.sub}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">Volume</h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
          {volumeTiles.map((t) => (
            <Card key={t.label}>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">{t.label}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold">{t.value}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Recent Conversations</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="text-right">Messages</TableHead>
                <TableHead className="text-right">Cost</TableHead>
                <TableHead className="text-right">Tokens</TableHead>
                <TableHead>Build?</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {recent.map((conv) => (
                <TableRow key={conv.id}>
                  <TableCell className="font-medium">{conv.title ?? 'Untitled'}</TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {formatDate(conv.createdAt)}
                  </TableCell>
                  <TableCell className="text-right">{conv._count.messages}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {fmtCost(num(conv.totalCostUsd))}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground text-sm">
                    {(conv.totalTokensIn + conv.totalTokensOut).toLocaleString()}
                  </TableCell>
                  <TableCell>
                    {conv.reachedRecommendation ? (
                      <span className="text-green-600 dark:text-green-500">✓</span>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
