package listings

import "testing"

// Mirrors backend/app/crud/reference_builds.py::_amazon_product_url's test
// cases: stored URL wins verbatim; otherwise build a /dp/{asin} link, with
// the tag query param only appended when an associate tag is configured.
func TestAmazonURL(t *testing.T) {
	stored := "https://www.amazon.com/some/existing/path"
	empty := ""

	cases := []struct {
		name         string
		storedURL    *string
		asin         string
		associateTag string
		want         string
	}{
		{"stored url wins", &stored, "B000123456", "mytag-20", stored},
		{"stored empty string falls through to built url", &empty, "B000123456", "mytag-20", "https://www.amazon.com/dp/B000123456?tag=mytag-20"},
		{"nil url, with tag", nil, "B000123456", "mytag-20", "https://www.amazon.com/dp/B000123456?tag=mytag-20"},
		{"nil url, no tag", nil, "B000123456", "", "https://www.amazon.com/dp/B000123456"},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := AmazonURL(c.storedURL, c.asin, c.associateTag)
			if got != c.want {
				t.Errorf("AmazonURL(%v, %q, %q) = %q, want %q", c.storedURL, c.asin, c.associateTag, got, c.want)
			}
		})
	}
}

func TestEbayURL(t *testing.T) {
	search := "https://www.ebay.com/sch/i.html?_nkw=RTX+4070&LH_BIN=1"
	noQuery := "https://www.ebay.com/deals"
	empty := ""
	prewrapped := "https://www.ebay.com/sch/i.html?_nkw=RTX+4070&campid=5338999999"
	track := "&mkevt=1&mkcid=1&mkrid=" + ebayUSRotationID + "&campid=5338999999&toolid=10001"

	cases := []struct {
		name       string
		storedURL  *string
		campaignID string
		want       string
	}{
		{"nil url returns empty", nil, "5338999999", ""},
		{"empty url returns empty", &empty, "5338999999", ""},
		{"no campaign id returns url verbatim", &search, "", search},
		{"appends tracking to url with existing query", &search, "5338999999", search + track},
		{"appends tracking to url without query", &noQuery, "5338999999", noQuery + "?" + "mkevt=1&mkcid=1&mkrid=" + ebayUSRotationID + "&campid=5338999999&toolid=10001"},
		{"already-wrapped url is returned verbatim", &prewrapped, "5338999999", prewrapped},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := EbayURL(c.storedURL, c.campaignID)
			if got != c.want {
				t.Errorf("EbayURL(%v, %q) = %q, want %q", c.storedURL, c.campaignID, got, c.want)
			}
		})
	}
}
