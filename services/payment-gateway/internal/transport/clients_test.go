package transport_test

import (
	"context"
	"net"
	"net/http"
	"testing"

	paymentv1 "github.com/meillaya/flow-qs/services/payment-gateway/gen/proto/payment/v1"
	"github.com/meillaya/flow-qs/services/payment-gateway/internal/payment"
	"github.com/meillaya/flow-qs/services/payment-gateway/internal/server"
	"github.com/meillaya/flow-qs/services/payment-gateway/internal/transport"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"google.golang.org/grpc"
)

func TestPaymentClient_InteroperatesWithInstrumentedServer(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	require.NoError(t, err)
	grpcServer := grpc.NewServer(grpc.StatsHandler(otelgrpc.NewServerHandler()))
	paymentv1.RegisterPaymentServiceServer(grpcServer, server.NewPaymentServer(payment.NewStore()))
	go func() { _ = grpcServer.Serve(listener) }()
	t.Cleanup(func() {
		grpcServer.Stop()
		_ = listener.Close()
	})

	client, conn, err := transport.NewPaymentClient(listener.Addr().String())
	require.NoError(t, err)
	t.Cleanup(func() { _ = conn.Close() })

	response, err := client.Charge(context.Background(), &paymentv1.ChargeRequest{
		OrderId: "otel-client", AmountCents: 100, CardToken: "tok",
	})
	require.NoError(t, err)
	assert.Equal(t, paymentv1.Status_APPROVED, response.Status)
}

func TestHTTPClient_UsesInstrumentedTransport(t *testing.T) {
	client := transport.NewHTTPClient(http.DefaultTransport)
	assert.NotEqual(t, http.DefaultTransport, client.Transport)
}
