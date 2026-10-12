// Command api starts the FPL data service as a gRPC server.
//
// There is no HTTP server: langgraph_service is the only caller and talks gRPC.
// For manual testing, server reflection is enabled so grpcurl works:
//
//	grpcurl -plaintext localhost:50051 list squadscout.v1.FplDataService
//	grpcurl -plaintext -d '{"name":"saka"}' localhost:50051 squadscout.v1.FplDataService/SearchPlayers
//	grpcurl -plaintext -d '{"player_id":12}' localhost:50051 squadscout.v1.FplDataService/GetPlayerRecentForm
//	grpcurl -plaintext -d '{"player_id":12}' localhost:50051 squadscout.v1.FplDataService/GetPlayerFixtures
//	grpcurl -plaintext -d '{"player_id":12,"gameweek":1}' localhost:50051 squadscout.v1.FplDataService/GetPlayerGameweekStats
//	grpcurl -plaintext -d '{"team_name":"Arsenal","next_n":3}' localhost:50051 squadscout.v1.FplDataService/GetFixtures
//	grpcurl -plaintext -d '{"team_id":6}' localhost:50051 squadscout.v1.FplDataService/GetManagerTeam
//	grpcurl -plaintext -d '{"team_id":6,"player_out_ids":[124],"player_in_ids":[12],"max_hit":4}' localhost:50051 squadscout.v1.FplDataService/EvaluateTransfers
//
// Environment variables: GRPC_ADDR (default :50051), DB_PATH (default
// data/fpl.db), FPL_BASE_URL (default the real FPL API).
//
// Startup order: open the SQLite database -> build the gRPC server -> register
// our service on it -> listen on a TCP port -> serve until Ctrl+C.
package main

import (
	"context"
	"log/slog"
	"net"
	"os"
	"os/signal"
	"syscall"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/reflection"

	// "pb" is a short alias for the code generated from fpl.proto by `make proto`.
	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
	"squad_scout/fpl_data_service/internal/db"
	"squad_scout/fpl_data_service/internal/fpl"
	"squad_scout/fpl_data_service/internal/handlers"
	"squad_scout/fpl_data_service/internal/manager"
	"squad_scout/fpl_data_service/internal/refresh"
)

// main is where every Go program starts.
func main() {
	// slog = Go's structured logger: prints lines like `level=INFO msg=... addr=:50051`.
	logger := slog.New(slog.NewTextHandler(os.Stdout, nil))
	slog.SetDefault(logger)

	addr := getenv("GRPC_ADDR", ":50051")
	dbPath := getenv("DB_PATH", "data/fpl.db")

	// A context is Go's standard "cancel signal" that gets passed around.
	// This one is cancelled when you press Ctrl+C (SIGINT) or the process is
	// asked to stop (SIGTERM), which triggers the graceful shutdown below.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop() // `defer` runs this line when main() returns, like a finally block.

	// Go has no exceptions: functions return an error value, and the caller
	// checks `if err != nil` right away. You will see this pattern everywhere.
	store, err := db.Open(ctx, dbPath)
	if err != nil {
		slog.Error("open database", "path", dbPath, "err", err)
		os.Exit(1)
	}
	defer store.Close()

	// Downloads from the FPL API into the store. Data older than an hour is
	// considered stale; FPL changes prices daily and points after matches.
	fplClient := fpl.NewClient(getenv("FPL_BASE_URL", fpl.DefaultBaseURL))
	refresher := refresh.New(store, fplClient, time.Hour)
	// Managers' teams only change at a deadline; keeping each for 5 minutes
	// means a conversation about one team downloads it once.
	managers := manager.NewLoader(fplClient, store, 5*time.Minute)

	// refreshAll updates players, teams, gameweeks and fixtures if they're
	// stale, and reports whether that worked. Failures only log a warning:
	// serving slightly old data beats not serving at all when the FPL API is
	// down (or rate-limiting us with 429s). The timeout stops a hanging FPL
	// API from blocking startup (or the next check) forever.
	refreshAll := func() bool {
		refreshCtx, cancel := context.WithTimeout(ctx, time.Minute)
		defer cancel()
		if err := refresher.All(refreshCtx); err != nil {
			slog.Warn("refresh failed, serving cached data; retrying in a minute", "err", err)
			return false
		}
		return true
	}
	refreshed := refreshAll() // before serving, so the first requests see fresh data

	// Create the gRPC server and attach our implementation of FplDataService
	// to it. After this, incoming RPCs (SearchPlayers, GetFixtures, ...) are
	// routed to the FplService methods in internal/handlers. The handlers use
	// the refresher to download a player's match history when asked about him,
	// and the manager loader to download a manager's team.
	srv := grpc.NewServer()
	pb.RegisterFplDataServiceServer(srv, handlers.NewFplService(store, refresher, managers))

	// Standard gRPC health check (used later by docker-compose healthchecks).
	healthSrv := health.NewServer()
	healthSrv.SetServingStatus("", healthpb.HealthCheckResponse_SERVING)
	healthpb.RegisterHealthServer(srv, healthSrv)

	// Lets grpcurl / Postman discover the API without the .proto file.
	reflection.Register(srv)

	// Open the TCP port (":50051" means every network interface, port 50051).
	lis, err := net.Listen("tcp", addr)
	if err != nil {
		slog.Error("listen", "addr", addr, "err", err)
		os.Exit(1)
	}

	// `go func() {...}()` starts a goroutine (a lightweight background thread).
	// It blocks on <-ctx.Done() until Ctrl+C, then stops the server cleanly:
	// GracefulStop lets in-flight requests finish first.
	go func() {
		<-ctx.Done()
		slog.Info("shutting down")
		srv.GracefulStop()
	}()

	// Re-check every 10 minutes. refreshAll only downloads data that's older
	// than an hour, so most checks do nothing but read sync_log. Checking more
	// often than the max age means data is never much more than an hour old.
	// After a failure, retry after 1 minute instead: if the cache is empty
	// (first start, or just after a schema change), every request fails
	// until a refresh succeeds.
	go func() {
		for {
			wait := 10 * time.Minute
			if !refreshed {
				wait = time.Minute
			}
			// select waits until one of its cases can proceed. time.After
			// returns a channel that receives a value once wait has passed.
			select {
			case <-ctx.Done(): // Ctrl+C: stop the loop
				return
			case <-time.After(wait):
				refreshed = refreshAll()
			}
		}
	}()

	slog.Info("fpl_data_service listening", "addr", addr, "db", dbPath)
	// Serve blocks here, handling requests, until GracefulStop is called.
	if err := srv.Serve(lis); err != nil {
		slog.Error("serve", "err", err)
		os.Exit(1)
	}
}

// getenv reads an environment variable, or returns fallback if it is unset.
func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
