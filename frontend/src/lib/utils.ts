import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * A *_cents amount as currency. Every price in this codebase is stored and
 * sent in cents (street_price_cents, approx_price, threshold_cents, …), so the
 * division belongs here rather than at each call site — a forgotten /100 is a
 * hundredfold price, which reads as plausible on an expensive part.
 */
export function formatCents(cents: number, currency = "USD"): string {
  return (cents / 100).toLocaleString("en-US", { style: "currency", currency })
}
