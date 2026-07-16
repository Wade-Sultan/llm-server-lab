'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import {
  BarChart3,
  Cpu,
  Monitor,
  CircuitBoard,
  Database,
  HardDrive,
  Zap,
  Box,
  Fan,
  Wind,
  BookMarked,
  Users,
  Layers,
  Gamepad2,
  AppWindow,
  BrainCircuit,
} from 'lucide-react';

type NavItem =
  | { type: 'separator'; label: string }
  | { type?: undefined; label: string; href: string; icon: React.ElementType };

const navItems: NavItem[] = [
  { label: 'Analytics', href: '/analytics', icon: BarChart3 },
  { type: 'separator', label: 'PC Parts' },
  { label: 'CPUs', href: '/cpus', icon: Cpu },
  { label: 'GPUs', href: '/gpus', icon: Monitor },
  { label: 'Motherboards', href: '/motherboards', icon: CircuitBoard },
  { label: 'RAM', href: '/ram', icon: Database },
  { label: 'Storage', href: '/storage', icon: HardDrive },
  { label: 'Cases', href: '/cases', icon: Box },
  { label: 'PSUs', href: '/psus', icon: Zap },
  { label: 'Fans', href: '/fans', icon: Fan },
  { label: 'CPU Coolers', href: '/cpu-coolers', icon: Wind },
  { type: 'separator', label: 'Component Groups' },
  { label: 'GPU Chipsets', href: '/gpu-chipsets', icon: Layers },
  { label: 'PSU Groups', href: '/psu-groups', icon: Layers },
  { label: 'RAM Groups', href: '/ram-groups', icon: Layers },
  { label: 'Storage Groups', href: '/storage-groups', icon: Layers },
  { type: 'separator', label: 'Catalog' },
  { label: 'Reference Builds', href: '/reference-builds', icon: BookMarked },
  { label: 'Games', href: '/games', icon: Gamepad2 },
  { label: 'Software', href: '/software', icon: AppWindow },
  { label: 'AI Models', href: '/ai-models', icon: BrainCircuit },
  { type: 'separator', label: 'Users' },
  { label: 'Users', href: '/users', icon: Users },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-60 flex-shrink-0 flex flex-col h-screen bg-sidebar border-r border-sidebar-border overflow-y-auto">
      <div className="px-5 py-5 border-b border-sidebar-border">
        <h1 className="text-sm font-semibold text-sidebar-primary tracking-wider uppercase">
          Palladium
        </h1>
        <p className="text-xs text-muted-foreground mt-0.5">Admin Dashboard</p>
      </div>
      <nav className="flex-1 px-3 py-3 space-y-0.5">
        {navItems.map((item, i) => {
          if (item.type === 'separator') {
            return (
              <div key={i} className="pt-4 pb-1 px-2">
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  {item.label}
                </span>
              </div>
            );
          }
          const Icon = item.icon;
          const isActive = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                'flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors',
                isActive
                  ? 'bg-sidebar-accent text-sidebar-primary font-medium'
                  : 'text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground'
              )}
            >
              <Icon className="w-4 h-4 flex-shrink-0" />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
