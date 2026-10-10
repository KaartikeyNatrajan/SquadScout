package names

import (
	"strings"
	"unicode"

	"golang.org/x/text/runes"
	"golang.org/x/text/transform"
	"golang.org/x/text/unicode/norm"
)

// special maps letters that NFD can't split into a base letter plus an accent
// (ø is its own letter, not o + mark), so Normalize replaces them by hand.
var special = strings.NewReplacer("ø", "o", "Ø", "o", "ß", "ss", "đ", "d", "Đ", "d", "ı", "i")

// Normalize lowercases a name and strips accents so searches ignore them:
// "Ødegaard" -> "odegaard", "Magalhães" -> "magalhaes".
func Normalize(s string) string {
	// NFD splits "é" into "e" + accent mark, runes.Remove drops all marks
	// (Mn = "nonspacing mark"), and NFC recombines whatever is left.
	t := transform.Chain(norm.NFD, runes.Remove(runes.In(unicode.Mn)), norm.NFC)
	out, _, err := transform.String(t, s)
	if err != nil {
		return strings.ToLower(s)
	}
	return strings.ToLower(special.Replace(out))
}
