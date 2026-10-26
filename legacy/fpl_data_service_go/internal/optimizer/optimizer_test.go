package optimizer

import (
	"context"
	"errors"
	"testing"
)

// Placeholder until the search is written: pins down that Suggest says so
// clearly, rather than returning an empty (and misleading) list. Replace this
// with real tests along with the implementation.
func TestSuggest_NotImplemented(t *testing.T) {
	suggestions, err := Suggest(context.Background(), Request{})
	if !errors.Is(err, ErrNotImplemented) || suggestions != nil {
		t.Errorf("want ErrNotImplemented and no suggestions, got %v, %v", suggestions, err)
	}
}
