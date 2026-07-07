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
