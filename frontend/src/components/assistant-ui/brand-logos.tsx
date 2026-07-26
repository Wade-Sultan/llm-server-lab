/**
 * Self-contained inline-SVG recreations of the Amazon and eBay wordmarks, used
 * as the marketplace buy-button glyphs. They're approximations built from a
 * system sans-serif (no external fonts/assets), but they carry each brand's
 * signature: eBay's four-color lowercase letters and Amazon's orange smile
 * arrow. Amazon's letters use currentColor so the wordmark stays legible in
 * both light and dark themes.
 */

const SANS = "Arial, Helvetica, sans-serif"

// Official eBay letter colors.
const EBAY_COLORS = {
  e: "#E53238",
  b: "#0064D2",
  a: "#F5AF02",
  y: "#86B817",
} as const

export function EbayLogo({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 58 26"
      className={className}
      role="img"
      aria-hidden="true"
      focusable="false"
    >
      <text
        x="1"
        y="20"
        fontFamily={SANS}
        fontSize="24"
        fontWeight={700}
        fontStyle="italic"
        letterSpacing="-1"
      >
        <tspan fill={EBAY_COLORS.e}>e</tspan>
        <tspan fill={EBAY_COLORS.b}>b</tspan>
        <tspan fill={EBAY_COLORS.a}>a</tspan>
        <tspan fill={EBAY_COLORS.y}>y</tspan>
      </text>
    </svg>
  )
}

const AMAZON_ORANGE = "#FF9900"

export function AmazonLogo({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 78 26"
      className={className}
      role="img"
      aria-hidden="true"
      focusable="false"
    >
      <text
        x="1"
        y="17"
        fontFamily={SANS}
        fontSize="18"
        fontWeight={700}
        letterSpacing="-0.5"
        fill="currentColor"
      >
        amazon
      </text>
      {/* The "smile" arrow curving from under the a to under the n. */}
      <path
        d="M5 20.5 C24 26.5, 52 26.5, 70 20.5"
        fill="none"
        stroke={AMAZON_ORANGE}
        strokeWidth="2.2"
        strokeLinecap="round"
      />
      {/* Arrowhead flicking up at the right end. */}
      <path d="M70 20.5 l-4.2 -0.6 l2 3.6 z" fill={AMAZON_ORANGE} />
    </svg>
  )
}
