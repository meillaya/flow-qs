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
	"go.opentelemetry.io/otel/trace"
	"google.golang.org/grpc"
	"google.golang.org/grpc/reflection"

	paymentv1 "github.com/meillaya/flow-qs/services/payment-gateway/gen/proto/payment/v1"
	"github.com/meillaya/flow-qs/services/payment-gateway/internal/handlers"
	"github.com/meillaya/flow-qs/services/payment-gateway/internal/metrics"
	"github.com/meillaya/flow-qs/services/payment-gateway/internal/otel"
	"github.com/meillaya/flow-qs/services/payment-gateway/internal/payment"
	"github.com/meillaya/flow-qs/services/payment-gateway/internal/server"
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
	metricRegistry := metrics.NewRegistry("payment-gateway")
	r.Use(metricRegistry.Middleware)
	r.Use(otelhttp.NewMiddleware("payment-gateway"))
	r.Use(requestLogger(logger))
	r.Get("/healthz", handlers.HealthzHandler)
	r.Get("/readyz", handlers.ReadyzHandler)
	r.Get("/metrics", metricRegistry.Handler().ServeHTTP)
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

type requestLogWriter struct {
	http.ResponseWriter
	statusCode  int
	wroteHeader bool
}

func (w *requestLogWriter) WriteHeader(statusCode int) {
	if w.wroteHeader {
		return
	}
	w.wroteHeader = true
	w.statusCode = statusCode
	w.ResponseWriter.WriteHeader(statusCode)
}

func (w *requestLogWriter) Write(body []byte) (int, error) {
	if !w.wroteHeader {
		w.WriteHeader(http.StatusOK)
	}
	return w.ResponseWriter.Write(body)
}

func requestLogger(logger *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
			if request.URL.Path == "/healthz" || request.URL.Path == "/readyz" || request.URL.Path == "/metrics" {
				next.ServeHTTP(response, request)
				return
			}
			wrapped := &requestLogWriter{ResponseWriter: response, statusCode: http.StatusOK}
			started := time.Now()
			next.ServeHTTP(wrapped, request)
			attributes := []any{
				"method", request.Method,
				"path", request.URL.Path,
				"status", wrapped.statusCode,
				"duration_ms", time.Since(started).Milliseconds(),
			}
			spanContext := trace.SpanContextFromContext(request.Context())
			if spanContext.IsValid() {
				attributes = append(attributes, "trace_id", spanContext.TraceID().String())
			}
			logger.InfoContext(request.Context(), "http request", attributes...)
		})
	}
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
