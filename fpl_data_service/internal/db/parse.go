package db

import (
	"fmt"
	"strconv"
)

// floatParser converts FPL's numeric strings ("6.2") to float64 and
// remembers the first failure. A loop can then parse many fields and check
// for an error once at the end, instead of after every single field:
//
//	var fp floatParser
//	form := fp.parse("form", p.Form)
//	xG := fp.parse("expected_goals", p.ExpectedGoals)
//	if fp.err != nil { ... }
//
// After the first failure, parse does nothing and returns 0, so the error
// that's reported is always the first one.
type floatParser struct {
	err error
}

func (fp *floatParser) parse(field, value string) float64 {
	if fp.err != nil {
		return 0
	}
	f, err := strconv.ParseFloat(value, 64)
	if err != nil {
		fp.err = fmt.Errorf("parse %s %q: %w", field, value, err)
	}
	return f
}
