// Package payment holds the core charge logic and in-memory state.
package payment

import (
	"crypto/rand"
	"errors"
	"fmt"
	"math/big"
	"os"
	"strconv"
	"strings"
	"sync"

	paymentv1 "github.com/meillaya/flow-qs/services/payment-gateway/gen/proto/payment/v1"
)

// ThresholdAmountCents is the maximum amount that is auto-approved.
const ThresholdAmountCents int64 = 100000

// ErrInvalidCharge identifies rejected charge input at transport boundaries.
var ErrInvalidCharge = errors.New("invalid charge")

// Charge represents a persisted charge record.
type Charge struct {
	ChargeID    string
	OrderID     string
	AmountCents int64
	Status      paymentv1.Status
}

// Store holds charges in memory.
type Store struct {
	mu      sync.RWMutex
	charges map[string]*Charge
}

func cloneCharge(charge *Charge) *Charge {
	if charge == nil {
		return nil
	}
	snapshot := *charge
	return &snapshot
}

// NewStore creates an empty Store.
func NewStore() *Store {
	return &Store{charges: make(map[string]*Charge)}
}

// failureRate returns the configured random failure probability.
func failureRate() float64 {
	raw := os.Getenv("FAILURE_RATE")
	if raw == "" {
		return 0.0
	}
	v, err := strconv.ParseFloat(raw, 64)
	if err != nil || v < 0 || v > 1 {
		return 0.0
	}
	return v
}

// randomDecline returns true with probability rate.
func randomDecline(rate float64) bool {
	if rate <= 0 {
		return false
	}
	n, err := rand.Int(rand.Reader, big.NewInt(1000))
	if err != nil {
		return false
	}
	return float64(n.Int64())/1000.0 < rate
}

// DecideStatus returns APPROVED or DECLINED based on amount and FAILURE_RATE.
func DecideStatus(amountCents int64) paymentv1.Status {
	if amountCents >= ThresholdAmountCents {
		return paymentv1.Status_DECLINED
	}
	if randomDecline(failureRate()) {
		return paymentv1.Status_DECLINED
	}
	return paymentv1.Status_APPROVED
}

// Charge creates a new charge and stores it.
func (s *Store) Charge(orderID string, amountCents int64, cardToken string) (*Charge, error) {
	if strings.TrimSpace(orderID) == "" || amountCents <= 0 || strings.TrimSpace(cardToken) == "" {
		return nil, ErrInvalidCharge
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	chargeID := fmt.Sprintf("ch_%s_%d", orderID, len(s.charges)+1)
	c := &Charge{
		ChargeID:    chargeID,
		OrderID:     orderID,
		AmountCents: amountCents,
		Status:      DecideStatus(amountCents),
	}
	s.charges[chargeID] = c
	return cloneCharge(c), nil
}

// Get returns a charge by ID or nil if missing.
func (s *Store) Get(chargeID string) *Charge {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return cloneCharge(s.charges[chargeID])
}

// Refund marks a charge as refunded and returns the updated charge.
func (s *Store) Refund(chargeID string) *Charge {
	s.mu.Lock()
	defer s.mu.Unlock()
	c := s.charges[chargeID]
	if c == nil {
		return nil
	}
	c.Status = paymentv1.Status_REFUNDED
	return cloneCharge(c)
}

// Len returns the number of stored charges.
func (s *Store) Len() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return len(s.charges)
}
