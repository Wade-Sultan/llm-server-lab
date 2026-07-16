import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { z } from 'zod';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(date: Date | string) {
  return new Date(date).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

export function splitCommaList(value: string): string[] {
  return value
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

export function joinCommaList(arr: string[]): string {
  return arr.join(', ');
}

export function centsToUsd(cents: number | null): number | null {
  return cents == null ? null : cents / 100;
}

export function usdToCents(usd: number | null): number | null {
  return usd == null ? null : Math.round(usd * 100);
}

export function formatUsd(cents: number | null): string {
  return cents == null
    ? '—'
    : (cents / 100).toLocaleString('en-US', { style: 'currency', currency: 'USD' });
}

export function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export const asinSchema = z
  .string()
  .min(1, 'ASIN is required')
  .refine((v) => /^[A-Z0-9]{10}$/i.test(v), { message: 'ASIN must be 10 characters (letters/numbers)' });
