package squad

import (
	"strings"
	"testing"
)

// validSquad builds a legal squad of made-up players: 2 GK, 5 DEF, 5 MID,
// 3 FWD, spread over clubs 1-8 so no club has more than 2, every player
// worth £5.0m, and £1.0m in the bank. Player ids are 1-15.
func validSquad() Squad {
	positions := []int{1, 1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4, 4, 4}
	s := Squad{Bank: 10}
	for i, pos := range positions {
		s.Members = append(s.Members, Member{
			PlayerID: i + 1,
			Name:     "P" + string(rune('A'+i)), // PA, PB, ...
			Position: pos,
			ClubID:   i%8 + 1,
			ClubName: "Club" + string(rune('1'+i%8)),
			Price:    50,
		})
	}
	return s
}

// hasProblem reports whether any problem message contains text.
func hasProblem(problems []string, text string) bool {
	for _, p := range problems {
		if strings.Contains(p, text) {
			return true
		}
	}
	return false
}

func TestValidate_ValidSquad(t *testing.T) {
	if problems := Validate(validSquad()); len(problems) != 0 {
		t.Errorf("want no problems, got %v", problems)
	}
}

func TestValidate_BrokenRules(t *testing.T) {
	cases := []struct {
		name      string
		breakRule func(s *Squad) // changes a valid squad so it breaks one rule
		want      string         // text the problem message must contain
	}{
		{"too few players", func(s *Squad) { s.Members = s.Members[:14] }, "has 14 players"},
		{"wrong position mix", func(s *Squad) { s.Members[2].Position = 3 }, "6 midfielders"},
		{"too many from one club", func(s *Squad) {
			for i := 0; i < 4; i++ {
				s.Members[i].ClubID, s.Members[i].ClubName = 9, "Arsenal"
			}
		}, "4 Arsenal players"},
		{"over budget", func(s *Squad) { s.Bank = -3 }, "£0.3m more than the budget"},
		{"duplicate player", func(s *Squad) { s.Members[1].PlayerID = 1 }, "twice"},
	}
	for _, tc := range cases {
		s := validSquad()
		tc.breakRule(&s)
		if problems := Validate(s); !hasProblem(problems, tc.want) {
			t.Errorf("%s: want a problem mentioning %q, got %v", tc.name, tc.want, problems)
		}
	}
}

func TestApply_LegalTransfer(t *testing.T) {
	s := validSquad()
	// Sell forward 13 (£5.0m) for a £5.5m forward: the bank goes 1.0 -> 0.5.
	in := Member{PlayerID: 100, Name: "New", Position: 4, ClubID: 9, ClubName: "Club9", Price: 55}

	next, problems := Apply(s, []int{13}, []Member{in})

	if len(problems) != 0 {
		t.Fatalf("want no problems, got %v", problems)
	}
	if next.Bank != 5 {
		t.Errorf("bank: want 5 (£0.5m), got %d", next.Bank)
	}
	if len(next.Members) != Size {
		t.Errorf("want %d players, got %d", Size, len(next.Members))
	}
	// The original squad must be untouched (Apply returns a new one).
	if s.Bank != 10 || s.Members[12].PlayerID != 13 {
		t.Error("Apply modified the squad it was given")
	}
}

func TestApply_Problems(t *testing.T) {
	forward := func(id, price int) Member {
		return Member{PlayerID: id, Name: "Fwd", Position: 4, ClubID: 9, ClubName: "Club9", Price: price}
	}
	cases := []struct {
		name string
		out  []int
		in   []Member
		want string
	}{
		{"counts differ", []int{13, 14}, []Member{forward(100, 50)}, "2 players out but 1 in"},
		{"selling someone not in the squad", []int{99}, []Member{forward(100, 50)}, "not in the squad"},
		{"buying someone already in the squad", []int{13}, []Member{forward(14, 50)}, "already in the squad"},
		{"wrong position", []int{13}, []Member{{PlayerID: 100, Name: "Mid", Position: 3, ClubID: 9, Price: 50}}, "6 midfielders"},
		{"can't afford", []int{13}, []Member{forward(100, 70)}, "£1.0m more than the budget"},
	}
	for _, tc := range cases {
		if _, problems := Apply(validSquad(), tc.out, tc.in); !hasProblem(problems, tc.want) {
			t.Errorf("%s: want a problem mentioning %q, got %v", tc.name, tc.want, problems)
		}
	}
}

func TestApply_ClubLimitCountsNewPlayers(t *testing.T) {
	s := validSquad()
	// Club 1 already has players 1 and 9. Bringing in two more Club 1
	// players (for forwards from other clubs) makes 4.
	in := []Member{
		{PlayerID: 100, Name: "A", Position: 4, ClubID: 1, ClubName: "Club1", Price: 50},
		{PlayerID: 101, Name: "B", Position: 4, ClubID: 1, ClubName: "Club1", Price: 50},
	}
	_, problems := Apply(s, []int{14, 15}, in)
	if !hasProblem(problems, "4 Club1 players") {
		t.Errorf("want the club limit broken, got %v", problems)
	}
}

func TestHitCost(t *testing.T) {
	cases := []struct {
		transfers, free int
		chip            string
		want            int
	}{
		{1, 1, "", 0},           // free
		{2, 1, "", 4},           // one extra
		{3, 1, "", 8},           // two extra
		{0, 2, "", 0},           // none made
		{5, 1, ChipWildcard, 0}, // a Wildcard makes all free
		{5, 1, ChipFreeHit, 0},
		{2, 1, "bboost", 4}, // Bench Boost doesn't
	}
	for _, tc := range cases {
		if got := HitCost(tc.transfers, tc.free, tc.chip); got != tc.want {
			t.Errorf("HitCost(%d, %d, %q): want %d, got %d", tc.transfers, tc.free, tc.chip, tc.want, got)
		}
	}
}
