package handlers

// Conversions from database rows (package db) to protobuf messages (package
// pb). Keeping them here means the db package never depends on gRPC, and
// each RPC handler stays short.
//
// Two conversions appear again and again:
//   - int -> int32: protobuf uses int32, Go's database code uses int, and Go
//     never converts between number types on its own.
//   - *int -> *int32: for "optional" proto fields, where nil means "not set".

import (
	"math"

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
	"squad_scout/fpl_data_service/internal/db"
)

// availabilityByStatus spells out FPL's one-letter status codes, so the
// model reads "injured" instead of having to know what "i" means.
var availabilityByStatus = map[string]string{
	"a": "available",
	"d": "doubtful",
	"i": "injured",
	"s": "suspended",
	"u": "unavailable", // e.g. left the club, or on loan outside the league
	"n": "ineligible",  // e.g. on loan at a club that can't field him against his parent club
}

func availability(status string) string {
	if a, ok := availabilityByStatus[status]; ok {
		return a
	}
	return "unknown"
}

func toPlayer(p db.PlayerRow) *pb.Player {
	return &pb.Player{
		Id:         int32(p.ID),
		WebName:    p.WebName,
		FirstName:  p.FirstName,
		SecondName: p.SecondName,
		TeamId:     int32(p.TeamID),
		TeamName:   p.TeamName,
		// The db's 1-4 match the proto enum's numbers, so a cast is enough.
		Position:                 pb.Position(p.Position),
		Price:                    pounds(p.NowCost),
		TotalPoints:              int32(p.TotalPoints),
		Form:                     p.Form,
		SelectedByPercent:        p.SelectedByPercent,
		Status:                   p.Status,
		Availability:             availability(p.Status),
		News:                     p.News,
		ChanceOfPlayingNextRound: int32Ptr(p.ChanceOfPlayingNextRound),
		Minutes:                  int32(p.Minutes),
		Starts:                   int32(p.Starts),
		GoalsScored:              int32(p.GoalsScored),
		Assists:                  int32(p.Assists),
		CleanSheets:              int32(p.CleanSheets),
		Bonus:                    int32(p.Bonus),
		ExpectedGoals:            p.ExpectedGoals,
		ExpectedAssists:          p.ExpectedAssists,
		ExpectedGoalInvolvements: p.ExpectedGoalInvolvements,
		PointsPerGame:            p.PointsPerGame,
		ExpectedPointsNext:       p.ExpectedPointsNext,
		TransfersInEvent:         int32(p.TransfersInEvent),
		TransfersOutEvent:        int32(p.TransfersOutEvent),
		PriceChangeEvent:         pounds(p.CostChangeEvent),
		NewsAdded:                p.NewsAdded,
	}
}

func toFixture(f db.FixtureRow) *pb.Fixture {
	return &pb.Fixture{
		Id:             int32(f.ID),
		Gameweek:       int32(f.Gameweek),
		HomeTeamId:     int32(f.HomeTeamID),
		HomeTeamName:   f.HomeTeamName,
		AwayTeamId:     int32(f.AwayTeamID),
		AwayTeamName:   f.AwayTeamName,
		KickoffTime:    f.KickoffTime,
		HomeDifficulty: int32(f.HomeDifficulty),
		AwayDifficulty: int32(f.AwayDifficulty),
		Finished:       f.Finished,
		HomeScore:      int32Ptr(f.HomeScore),
		AwayScore:      int32Ptr(f.AwayScore),
	}
}

// toPlayerFixture turns a fixture into teamID's side of it: who they play,
// whether at home, and how hard it is for them.
func toPlayerFixture(f db.FixtureRow, teamID int) *pb.PlayerFixture {
	pf := &pb.PlayerFixture{
		FixtureId:   int32(f.ID),
		Gameweek:    int32(f.Gameweek),
		KickoffTime: f.KickoffTime,
	}
	if f.HomeTeamID == teamID {
		pf.IsHome = true
		pf.OpponentTeamId = int32(f.AwayTeamID)
		pf.OpponentTeamName = f.AwayTeamName
		pf.Difficulty = int32(f.HomeDifficulty)
	} else {
		pf.IsHome = false
		pf.OpponentTeamId = int32(f.HomeTeamID)
		pf.OpponentTeamName = f.HomeTeamName
		pf.Difficulty = int32(f.AwayDifficulty)
	}
	return pf
}

// toGameweekStats converts a player's gameweek. Each match goes into
// MatchResults; when there was exactly one (almost always), its details are
// also copied to the top-level fixture fields for convenience.
func toGameweekStats(g db.PlayerGameweekRow) *pb.GameweekStats {
	stats := &pb.GameweekStats{
		PlayerId:                 int32(g.PlayerID),
		Gameweek:                 int32(g.Gameweek),
		Minutes:                  int32(g.Minutes),
		Starts:                   int32(g.Starts),
		GoalsScored:              int32(g.GoalsScored),
		Assists:                  int32(g.Assists),
		CleanSheets:              int32(g.CleanSheets),
		GoalsConceded:            int32(g.GoalsConceded),
		Saves:                    int32(g.Saves),
		Bonus:                    int32(g.Bonus),
		YellowCards:              int32(g.YellowCards),
		RedCards:                 int32(g.RedCards),
		TotalPoints:              int32(g.TotalPoints),
		ExpectedGoals:            g.ExpectedGoals,
		ExpectedAssists:          g.ExpectedAssists,
		ExpectedGoalInvolvements: g.ExpectedGoalInvolvements,
	}
	for _, m := range g.Matches {
		stats.MatchResults = append(stats.MatchResults, &pb.MatchResult{
			FixtureId:        int32(m.FixtureID),
			OpponentTeamId:   int32(m.OpponentTeamID),
			OpponentTeamName: m.OpponentTeamName,
			WasHome:          m.WasHome,
			KickoffTime:      m.KickoffTime,
			TeamScore:        int32Ptr(m.TeamScore),
			OpponentScore:    int32Ptr(m.OpponentScore),
			Minutes:          int32(m.Minutes),
			TotalPoints:      int32(m.TotalPoints),
		})
	}
	if len(g.Matches) == 1 {
		m := stats.MatchResults[0]
		stats.FixtureId = m.FixtureId
		stats.OpponentTeamId = m.OpponentTeamId
		stats.OpponentTeamName = m.OpponentTeamName
		stats.WasHome = m.WasHome
		stats.KickoffTime = m.KickoffTime
		stats.TeamScore = m.TeamScore
		stats.OpponentScore = m.OpponentScore
	}
	return stats
}

// pounds converts tenths of £m (how FPL stores money) to £m: 95 -> 9.5.
// It converts to float before dividing: 95/10 in integer maths is 9, not 9.5.
func pounds(tenths int) float64 {
	return float64(tenths) / 10
}

// int32Ptr converts an optional int. nil stays nil ("not set").
func int32Ptr(p *int) *int32 {
	if p == nil {
		return nil
	}
	v := int32(*p)
	return &v
}

// round2 rounds to 2 decimal places. Adding up floats like 0.23 + 1.38 + 0.34
// gives 1.9499999999999997, not 1.95, so sums are rounded before sending.
func round2(x float64) float64 {
	return math.Round(x*100) / 100
}
