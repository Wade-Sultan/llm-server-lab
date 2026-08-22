// Package listings holds listing-related logic that doesn't belong in the
// store's query layer.
package listings

import (
	"fmt"
	"net/url"
	"strings"
)

// AmazonURL builds the outbound URL for an Amazon listing, mirroring
// backend/app/crud/reference_builds.py::_amazon_product_url: prefer the
// stored URL verbatim, otherwise construct a /dp/{asin} link and append the
// Associates tracking tag as a "tag" query param when configured. Links work
// without a tag, they're just unattributed until AssociateTag is set.
func AmazonURL(storedURL *string, asin, associateTag string) string {
	if storedURL != nil && *storedURL != "" {
		return *storedURL
	}
	url := fmt.Sprintf("https://www.amazon.com/dp/%s", asin)
	if associateTag != "" {
		url += "?tag=" + associateTag
	}
	return url
}

// ebayUSRotationID is eBay Partner Network's US-marketplace "rotation" id (the
// mkrid parameter), required alongside campid for EPN's custom-link click
// tracking. Palladium targets the US marketplace only today (USD prices,
// amazon.com parity), so it's a constant rather than configuration.
const ebayUSRotationID = "711-53200-19255-0"

// EbayURL wraps a stored eBay URL — typically a search-results page whose
// filters were configured via eBay Partner Network — with EPN custom-link
// tracking query params so clicks are attributed to campaignID. Mirrors
// AmazonURL's spirit: the link works without a campaign id (just unattributed),
// and a URL that's already wrapped (already carries campid) is returned
// verbatim so tracking params are never doubled up.
func EbayURL(storedURL *string, campaignID string) string {
	if storedURL == nil || *storedURL == "" {
		return ""
	}
	link := *storedURL
	if campaignID == "" || strings.Contains(link, "campid=") || isEPNShortLink(link) {
		return link
	}
	sep := "?"
	if strings.Contains(link, "?") {
		sep = "&"
	}
	return link + sep + "mkevt=1&mkcid=1&mkrid=" + ebayUSRotationID +
		"&campid=" + campaignID + "&toolid=10001"
}

// isEPNShortLink reports whether raw is one of eBay Partner Network's own
// shortened links, the https://ebay.us/aBcDeF form its link generator hands
// out.
//
// Those already carry their attribution inside the redirect they expand to, so
// the tracking this file appends does not belong on them: at best it is
// duplicated on the far side, and at worst the extra query string travels no
// further than the shortener, which is not obliged to forward it. The stored
// link is already the complete affiliate link, so it goes out untouched.
//
// Matched on the host rather than a prefix so that a path or query containing
// the string "ebay.us" cannot pass for one.
func isEPNShortLink(raw string) bool {
	u, err := url.Parse(raw)
	if err != nil {
		return false
	}
	return strings.EqualFold(u.Hostname(), "ebay.us")
}
