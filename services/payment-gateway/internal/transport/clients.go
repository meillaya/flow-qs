// Package transport constructs outbound clients with OpenTelemetry propagation.
package transport

import (
	"net/http"

	paymentv1 "github.com/meillaya/flow-qs/services/payment-gateway/gen/proto/payment/v1"
	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

// NewPaymentClient creates an instrumented gRPC payment client and its connection.
func NewPaymentClient(address string) (paymentv1.PaymentServiceClient, *grpc.ClientConn, error) {
	conn, err := grpc.NewClient(
		address,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithStatsHandler(otelgrpc.NewClientHandler()),
	)
	if err != nil {
		return nil, nil, err
	}
	return paymentv1.NewPaymentServiceClient(conn), conn, nil
}

// NewHTTPClient creates an HTTP client whose transport injects OTel context.
func NewHTTPClient(base http.RoundTripper) *http.Client {
	if base == nil {
		base = http.DefaultTransport
	}
	return &http.Client{Transport: otelhttp.NewTransport(base)}
}
