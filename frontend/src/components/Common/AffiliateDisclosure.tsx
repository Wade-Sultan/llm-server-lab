import { cn } from "@/lib/utils"

/**
 * The affiliate disclosure that has to sit next to every set of marketplace
 * buy links.
 *
 * Both programs require this and both require it *here*, not one click away:
 * the eBay Partner Network agreement (§I.G) states that a disclosure living
 * only on an "About Us", "Terms of Use" or "Legal" page linked from the
 * promotional content is not compliant — it has to be unavoidable and as close
 * to the links as possible. That is why this renders above the parts list
 * rather than in the card footer: a reader meets it before the first buy
 * button, not after.
 *
 * The wording follows EPN's own recommended phrasing ("As an eBay Partner, I
 * may be compensated if you make a purchase") and Amazon's required
 * "earn from qualifying purchases" construction, so neither program has to
 * accept a paraphrase. Change it in one place only — /about quotes each
 * program's sentence in full, and the two should not drift apart.
 */
export function AffiliateDisclosure({ className }: { className?: string }) {
  return (
    <p className={cn("text-muted-foreground text-xs", className)}>
      As an eBay Partner Network member, Palladium earns a commission
      on qualifying purchases made through the links below at no extra cost to
      you.
    </p>
  )
}
