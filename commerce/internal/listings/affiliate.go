// Package listings holds listing-related logic that doesn't belong in the
// store's query layer.
package listings

import (
	"fmt"
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
	url := *storedURL
	if campaignID == "" || strings.Contains(url, "campid=") {
		return url
	}
	sep := "?"
	if strings.Contains(url, "?") {
		sep = "&"
	}
	return url + sep + "mkevt=1&mkcid=1&mkrid=" + ebayUSRotationID +
		"&campid=" + campaignID + "&toolid=10001"
}
