package main

import (
	"context"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"
	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/reflection"

	paymentv1 "github.com/example/payment-gateway/gen/proto/payment/v1"
	"github.com/example/payment-gateway/internal/handlers"
	"github.com/example/payment-gateway/internal/otel"
	"github.com/example/payment-gateway/internal/payment"
	"github.com/example/payment-gateway/internal/server"
)

func main() {
	logger := slog.New(slog.NewTextHandler(os.Stdout, nil))
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	shutdown, err := otel.Init(ctx, "payment-gateway")
	if err != nil {
		logger.Error("failed to initialize otel", "error", err)
		os.Exit(1)
	}
	defer func() {
		if err := shutdown(context.Background()); err != nil {
			logger.Error("failed to shutdown otel", "error", err)
		}
	}()

	store := payment.NewStore()

	grpcPort := getEnv("GRPC_PORT", "50052")
	grpcLis, err := net.Listen("tcp", ":"+grpcPort)
	if err != nil {
		logger.Error("failed to listen grpc", "error", err)
		os.Exit(1)
	}
	grpcServer := grpc.NewServer(
		grpc.StatsHandler(otelgrpc.NewServerHandler()),
	)
	paymentv1.RegisterPaymentServiceServer(grpcServer, server.NewPaymentServer(store))
	reflection.Register(grpcServer)

	httpPort := getEnv("HTTP_PORT", "8005")
	r := chi.NewRouter()
	r.Use(otelhttp.NewMiddleware("payment-gateway"))
	r.Get("/healthz", handlers.HealthzHandler)
	r.Get("/readyz", handlers.ReadyzHandler)
	r.Post("/charge", handlers.ChargeHandler(store))

	httpServer := &http.Server{
		Addr:         ":" + httpPort,
		Handler:      r,
		ReadTimeout:  5 * time.Second,
		WriteTimeout: 10 * time.Second,
		IdleTimeout:  120 * time.Second,
	}

	errCh := make(chan error, 2)
	go func() {
		logger.Info("starting grpc server", "port", grpcPort)
		if err := grpcServer.Serve(grpcLis); err != nil {
			errCh <- fmt.Errorf("grpc server error: %w", err)
		}
	}()
	go func() {
		logger.Info("starting http server", "port", httpPort)
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			errCh <- fmt.Errorf("http server error: %w", err)
		}
	}()

	select {
	case <-ctx.Done():
		logger.Info("shutting down")
	case err := <-errCh:
		logger.Error("server error", "error", err)
	}

	grpcServer.GracefulStop()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = httpServer.Shutdown(shutdownCtx)
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
