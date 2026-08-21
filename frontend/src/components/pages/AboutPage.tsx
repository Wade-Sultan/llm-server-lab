import { AffiliateDisclosure } from "@/components/Common/AffiliateDisclosure"

function Section({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="space-y-2.5">
      <h2 className="font-medium tracking-tight">{title}</h2>
      <div className="space-y-2.5 text-sm text-muted-foreground">
        {children}
      </div>
    </section>
  )
}

export default function AboutPage() {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-6 py-10">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight">About</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            What Palladium is, and how it pays for itself
          </p>
        </div>

        <div className="space-y-8">
          <Section title="What Palladium is">
            <p>
              Palladium is an AI-powered PC building platform. You describe the
              machine you want, and it assembles a parts list checked against a
              database of compatibility rules, then tracks what those parts
              currently cost.
            </p>
          </Section>

          <Section title="How Palladium makes money">
            <p>
              Palladium takes part in the eBay Partner Network program. The
              marketplace buttons on a build are affiliate links, which means:
            </p>
            <ul className="ml-4 list-disc space-y-2">
              <li>
                As an eBay Partner, Palladium may be compensated if you make a
                purchase.
              </li>
              {/* <li>
                As an Amazon Associate, Palladium earns from qualifying
                purchases.
              </li> */}
            </ul>
            <p>
              <span className="font-medium text-foreground">
                You are never charged extra for this.
              </span>{" "}
              The price you pay on eBay {/*or Amazon*/} is the same whether you
              reach the listing through Palladium or go there yourself. The
              commission is paid by the marketplace out of its own cut, not
              added to your order.
            </p>
            <p>
              Palladium's recommendations are not connected to commissions:
              Builds are put together from compatibility and the price of the
              part, and a part is not ranked higher for paying more.
            </p>
          </Section>

          <Section title="How eBay data is handled">
            <p>
              Palladium does not connect to eBay's API and does not take in eBay
              listing data. The only thing we keep for the eBay button is the
              link itself (a filtered search URL set up through eBay Partner
              Network) and nothing else. We do not store item records, seller
              information, or prices. The affiliate tracking is added to that
              link at the moment the page is served.
            </p>
            <p>
              Those links are also kept apart from the parts data, and not by
              convention: the database enforces it. The recommender that
              assembles builds connects with an account that has no access to
              the listings tables at all, so it cannot read an eBay link
              whatever its code asks for; it works from Palladium's parts
              catalog and that catalog's own prices. The commerce service that
              serves the links to your browser connects with a different
              account, one that can read them but not change them. Writing a
              listing is possible only from the admin tool.
            </p>
            <p>
              The result is that no eBay listing data reaches an AI model. It is
              never placed in a model's context, and it never enters the records
              Palladium keeps to measure and improve its recommendations; those
              hold catalog parts and catalog prices. Because nothing is taken in
              from eBay, there is nothing to retain. No eBay listing data sits
              in a model, in training data, or in any set used to tune the
              system.
            </p>
          </Section>

          <Section title="Where prices come from">
            <p>
              The price shown against a part is an estimate, not a quote from
              any one shop. Once a night Palladium looks up the parts in its
              catalog through SerpApi, a third-party service that returns Google
              Shopping results. It searches on the part's own name and nothing
              else. No information about you or the build you are working on is
              sent, and the whole job runs against a fixed monthly search
              budget.
            </p>
            <p>
              Each result is then judged on its title. Whole prebuilt systems,
              bundles, multipacks, accessories, and anything sold as used,
              refurbished, open-box, or for parts are dropped, along with
              anything that doesn't closely match the part being priced. What is
              left is trimmed of outliers, and the middle value of the rest
              becomes the part's approximate price. That is why a build is
              labelled approximate: it is the middle of a spread of what
              retailers are asking, not an offer anyone has made to you.
            </p>
            <p>
              Because these are Google Shopping results, the merchants in them
              vary, and eBay is sometimes one of them. Where that happens, an
              eBay merchant's price counts toward the estimate like any other
              retailer's. That figure comes out of Google's results rather than
              from eBay itself. Palladium still makes no request to eBay and
              still holds no eBay listing data.
            </p>
          </Section>

          <Section title="Where you'll see this">
            <p>
              The same disclosure appears on every build, directly above the buy
              buttons, so you see it before you follow a link rather than only
              if you come looking for this page:
            </p>
            <div className="rounded-lg border bg-muted/40 px-4 py-3">
              <AffiliateDisclosure />
            </div>
          </Section>

          <Section title="Independence">
            <p>
              Palladium is an independent site. Beyond taking part in their
              affiliate programs, it is not affiliated with, endorsed by, or
              sponsored by eBay, Amazon, or any parts manufacturer, and nothing
              here is written or reviewed by them.
            </p>
          </Section>
        </div>
      </div>
    </div>
  )
}
