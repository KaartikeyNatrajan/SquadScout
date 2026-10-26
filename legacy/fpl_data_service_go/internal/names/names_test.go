package names

import "testing"

func TestNormalize(t *testing.T) {
	// Table-driven test: one {input, expected} pair per line, so each input
	// stays next to its expected output.
	cases := []struct {
		in, want string
	}{
		{"Ødegaard", "odegaard"},
		{"Groß", "gross"},
		{"dos Santos Magalhães", "dos santos magalhaes"},
		{"F.Kadıoğlu", "f.kadioglu"},
		{"Petrović", "petrovic"},
		{"Hjertø-Dahl", "hjerto-dahl"},
		{"Saka", "saka"},
	}
	for _, tc := range cases {
		if got := Normalize(tc.in); got != tc.want {
			t.Errorf("Normalize(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}
