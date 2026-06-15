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

export default async function AnalyticsPage() {
  const [totalConversations, totalMessages, userMessages, assistantMessages, recent] =
    await Promise.all([
      db.conversation.count(),
      db.message.count(),
      db.message.count({ where: { role: 'user' } }),
      db.message.count({ where: { role: 'assistant' } }),
      db.conversation.findMany({
        orderBy: { createdAt: 'desc' },
        take: 20,
        include: { _count: { select: { messages: true } } },
      }),
    ]);

  const stats = [
    { label: 'Total Conversations', value: totalConversations },
    { label: 'Total Messages', value: totalMessages },
    { label: 'User Messages', value: userMessages },
    { label: 'Assistant Messages', value: assistantMessages },
  ];

  return (
    <div className="p-8 space-y-8">
      <div>
        <h1 className="text-2xl font-bold">LLM Analytics</h1>
        <p className="text-muted-foreground mt-1">Conversation and message activity overview</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {stats.map((s) => (
          <Card key={s.label}>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">{s.label}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-3xl font-bold">{s.value.toLocaleString()}</p>
            </CardContent>
          </Card>
        ))}
      </div>

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
                <TableHead>Messages</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {recent.map((conv) => (
                <TableRow key={conv.id}>
                  <TableCell className="font-medium">{conv.title ?? 'Untitled'}</TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {formatDate(conv.createdAt)}
                  </TableCell>
                  <TableCell>{conv._count.messages}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
