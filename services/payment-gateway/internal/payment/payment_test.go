package payment_test

import (
	"os"
	"testing"

	"github.com/stretchr/testify/assert"

	"github.com/example/payment-gateway/internal/payment"
	paymentv1 "github.com/example/payment-gateway/gen/proto/payment/v1"
)

func TestDecideStatus_ApprovesSmallAmount(t *testing.T) {
	assert.Equal(t, paymentv1.Status_APPROVED, payment.DecideStatus(5000))
	assert.Equal(t, paymentv1.Status_APPROVED, payment.DecideStatus(99999))
}

func TestDecideStatus_DeclinesLargeAmount(t *testing.T) {
	assert.Equal(t, paymentv1.Status_DECLINED, payment.DecideStatus(100000))
	assert.Equal(t, paymentv1.Status_DECLINED, payment.DecideStatus(200000))
}

func TestDecideStatus_FailureRateAlwaysDeclines(t *testing.T) {
	t.Setenv("FAILURE_RATE", "1.0")
	for range 10 {
		assert.Equal(t, paymentv1.Status_DECLINED, payment.DecideStatus(5000))
	}
}

func TestStore_ChargeAndGet(t *testing.T) {
	s := payment.NewStore()
	c := s.Charge("o1", 5000, "tok")
	assert.NotEmpty(t, c.ChargeID)
	assert.Equal(t, paymentv1.Status_APPROVED, c.Status)

	got := s.Get(c.ChargeID)
	assert.Equal(t, c.ChargeID, got.ChargeID)
	assert.Equal(t, int64(5000), got.AmountCents)
}

func TestStore_Refund(t *testing.T) {
	s := payment.NewStore()
	c := s.Charge("o2", 5000, "tok")
	refunded := s.Refund(c.ChargeID)
	assert.Equal(t, paymentv1.Status_REFUNDED, refunded.Status)
	assert.Equal(t, paymentv1.Status_REFUNDED, s.Get(c.ChargeID).Status)
}

func TestMain(m *testing.M) {
	// Ensure FAILURE_RATE is not leaked between tests.
	os.Unsetenv("FAILURE_RATE")
	os.Exit(m.Run())
}
