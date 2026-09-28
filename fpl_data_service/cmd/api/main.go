// Command api starts the FPL data service as a gRPC server.
//
// There is no HTTP server: langgraph_service is the only caller and talks gRPC.
// For manual testing, server reflection is enabled so grpcurl works:
//
//	grpcurl -plaintext localhost:50051 list
//	grpcurl -plaintext -d '{"name":"saka"}' localhost:50051 squadscout.v1.FplDataService/SearchPlayers
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

	"google.golang.org/grpc"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/reflection"

	// "pb" is a short alias for the code generated from fpl.proto by `make proto`.
	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
	"squad_scout/fpl_data_service/internal/db"
	"squad_scout/fpl_data_service/internal/fpl"
	"squad_scout/fpl_data_service/internal/handlers"
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

	// HTTP client for the public FPL website API. Not used yet: SearchPlayers
	// still returns a hardcoded player (see internal/handlers).
	fplClient := fpl.NewClient()

	// Create the gRPC server and attach our implementation of FplDataService
	// to it. After this, incoming SearchPlayers calls are routed to
	// FplService.SearchPlayers in internal/handlers/fpl_service.go.
	srv := grpc.NewServer()
	pb.RegisterFplDataServiceServer(srv, handlers.NewFplService(store, fplClient))

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
