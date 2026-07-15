package server_test

import (
	"context"
	"net"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"

	paymentv1 "github.com/example/payment-gateway/gen/proto/payment/v1"
	"github.com/example/payment-gateway/internal/payment"
	"github.com/example/payment-gateway/internal/server"
)

func startTestServer(t *testing.T) (paymentv1.PaymentServiceClient, func()) {
	t.Helper()
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	require.NoError(t, err)

	grpcServer := grpc.NewServer()
	store := payment.NewStore()
	paymentv1.RegisterPaymentServiceServer(grpcServer, server.NewPaymentServer(store))

	go func() {
		_ = grpcServer.Serve(lis)
	}()

	conn, err := grpc.NewClient(lis.Addr().String(), grpc.WithTransportCredentials(insecure.NewCredentials()))
	require.NoError(t, err)

	cleanup := func() {
		_ = conn.Close()
		grpcServer.Stop()
	}
	return paymentv1.NewPaymentServiceClient(conn), cleanup
}

func TestCharge_Approved(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()

	resp, err := client.Charge(context.Background(), &paymentv1.ChargeRequest{
		OrderId:     "o1",
		AmountCents: 5000,
		CardToken:   "tok",
	})
	require.NoError(t, err)
	assert.Equal(t, paymentv1.Status_APPROVED, resp.Status)
	assert.NotEmpty(t, resp.ChargeId)
}

func TestCharge_Declined(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()

	resp, err := client.Charge(context.Background(), &paymentv1.ChargeRequest{
		OrderId:     "o2",
		AmountCents: 200000,
		CardToken:   "tok",
	})
	require.NoError(t, err)
	assert.Equal(t, paymentv1.Status_DECLINED, resp.Status)
}

func Test_Charge_returns_invalid_argument_when_request_is_invalid(t *testing.T) {
	t.Parallel()

	client, cleanup := startTestServer(t)
	defer cleanup()

	_, err := client.Charge(context.Background(), &paymentv1.ChargeRequest{
		OrderId: " ", AmountCents: -1, CardToken: " ",
	})

	require.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
}

func TestGetStatus(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()

	charge, err := client.Charge(context.Background(), &paymentv1.ChargeRequest{
		OrderId:     "o3",
		AmountCents: 1000,
		CardToken:   "tok",
	})
	require.NoError(t, err)

	status, err := client.GetStatus(context.Background(), &paymentv1.GetStatusRequest{
		ChargeId: charge.ChargeId,
	})
	require.NoError(t, err)
	assert.Equal(t, charge.ChargeId, status.ChargeId)
	assert.Equal(t, paymentv1.Status_APPROVED, status.Status)
	assert.Equal(t, int64(1000), status.AmountCents)
}

func TestRefund(t *testing.T) {
	client, cleanup := startTestServer(t)
	defer cleanup()

	charge, err := client.Charge(context.Background(), &paymentv1.ChargeRequest{
		OrderId:     "o4",
		AmountCents: 1000,
		CardToken:   "tok",
	})
	require.NoError(t, err)

	refund, err := client.Refund(context.Background(), &paymentv1.RefundRequest{
		ChargeId: charge.ChargeId,
	})
	require.NoError(t, err)
	assert.Equal(t, paymentv1.Status_REFUNDED, refund.Status)
}
