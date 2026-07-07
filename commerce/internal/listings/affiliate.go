// Package listings holds listing-related logic that doesn't belong in the
// store's query layer.
package listings

import "fmt"

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
