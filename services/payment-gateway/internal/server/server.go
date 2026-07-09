// Package server implements the gRPC PaymentService.
package server

import (
	"context"

	paymentv1 "github.com/example/payment-gateway/gen/proto/payment/v1"
	"github.com/example/payment-gateway/internal/payment"
)

// PaymentServer implements payment.v1.PaymentService.
type PaymentServer struct {
	paymentv1.UnimplementedPaymentServiceServer
	store *payment.Store
}

// NewPaymentServer creates a PaymentServer backed by store.
func NewPaymentServer(store *payment.Store) *PaymentServer {
	return &PaymentServer{store: store}
}

// Charge processes a charge request.
func (s *PaymentServer) Charge(_ context.Context, req *paymentv1.ChargeRequest) (*paymentv1.ChargeResponse, error) {
	c := s.store.Charge(req.OrderId, req.AmountCents, req.CardToken)
	return &paymentv1.ChargeResponse{
		ChargeId: c.ChargeID,
		Status:   c.Status,
	}, nil
}

// GetStatus returns the current status of a charge.
func (s *PaymentServer) GetStatus(_ context.Context, req *paymentv1.GetStatusRequest) (*paymentv1.GetStatusResponse, error) {
	c := s.store.Get(req.ChargeId)
	if c == nil {
		return &paymentv1.GetStatusResponse{
			ChargeId: req.ChargeId,
			Status:   paymentv1.Status_STATUS_UNSPECIFIED,
		}, nil
	}
	return &paymentv1.GetStatusResponse{
		ChargeId:    c.ChargeID,
		OrderId:     c.OrderID,
		AmountCents: c.AmountCents,
		Status:      c.Status,
	}, nil
}

// Refund refunds an existing charge.
func (s *PaymentServer) Refund(_ context.Context, req *paymentv1.RefundRequest) (*paymentv1.RefundResponse, error) {
	c := s.store.Refund(req.ChargeId)
	if c == nil {
		return &paymentv1.RefundResponse{
			ChargeId: req.ChargeId,
			Status:   paymentv1.Status_STATUS_UNSPECIFIED,
		}, nil
	}
	return &paymentv1.RefundResponse{
		ChargeId: c.ChargeID,
		Status:   c.Status,
	}, nil
}
