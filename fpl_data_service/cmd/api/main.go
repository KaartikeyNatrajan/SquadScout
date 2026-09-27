// Command api starts the FPL data service as a gRPC server.
//
// There is no HTTP server: langgraph_service is the only caller and talks gRPC.
// For manual testing, server reflection is enabled so grpcurl works:
//
//	grpcurl -plaintext localhost:50051 list
//	grpcurl -plaintext -d '{"name":"saka"}' localhost:50051 squadscout.v1.FplDataService/SearchPlayers
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

	pb "squad_scout/fpl_data_service/gen/squadscout/v1"
	"squad_scout/fpl_data_service/internal/db"
	"squad_scout/fpl_data_service/internal/fpl"
	"squad_scout/fpl_data_service/internal/handlers"
)

func main() {
	logger := slog.New(slog.NewTextHandler(os.Stdout, nil))
	slog.SetDefault(logger)

	addr := getenv("GRPC_ADDR", ":50051")
	dbPath := getenv("DB_PATH", "data/fpl.db")

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	store, err := db.Open(ctx, dbPath)
	if err != nil {
		slog.Error("open database", "path", dbPath, "err", err)
		os.Exit(1)
	}
	defer store.Close()

	fplClient := fpl.NewClient()

	srv := grpc.NewServer()
	pb.RegisterFplDataServiceServer(srv, handlers.NewFplService(store, fplClient))

	// Standard gRPC health check (used later by docker-compose healthchecks).
	healthSrv := health.NewServer()
	healthSrv.SetServingStatus("", healthpb.HealthCheckResponse_SERVING)
	healthpb.RegisterHealthServer(srv, healthSrv)

	// Lets grpcurl / Postman discover the API without the .proto file.
	reflection.Register(srv)

	lis, err := net.Listen("tcp", addr)
	if err != nil {
		slog.Error("listen", "addr", addr, "err", err)
		os.Exit(1)
	}

	go func() {
		<-ctx.Done()
		slog.Info("shutting down")
		srv.GracefulStop()
	}()

	slog.Info("fpl_data_service listening", "addr", addr, "db", dbPath)
	if err := srv.Serve(lis); err != nil {
		slog.Error("serve", "err", err)
		os.Exit(1)
	}
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
