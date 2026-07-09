// Package handlers provides HTTP handlers for the payment gateway.
package handlers

import (
	"encoding/json"
	"net/http"

	"github.com/example/payment-gateway/internal/payment"
)

// HTTPStore is the store interface used by HTTP handlers.
type HTTPStore interface {
	Charge(orderID string, amountCents int64, cardToken string) *payment.Charge
	Get(chargeID string) *payment.Charge
}

// ChargeRequest is the JSON body for POST /charge.
type ChargeRequest struct {
	OrderID     string `json:"order_id"`
	AmountCents int64  `json:"amount_cents"`
	CardToken   string `json:"card_token"`
}

// ChargeResponse is the JSON response for POST /charge.
type ChargeResponse struct {
	Status    string `json:"status"`
	ChargeID  string `json:"charge_id"`
	OrderID   string `json:"order_id"`
	Amount    int64  `json:"amount_cents"`
	CardToken string `json:"card_token"`
}

// statusText maps proto status to lowercase string.
func statusText(status int32) string {
	switch status {
	case 1:
		return "approved"
	case 2:
		return "declined"
	case 3:
		return "refunded"
	default:
		return "unknown"
	}
}

// ChargeHandler handles POST /charge.
func ChargeHandler(store HTTPStore) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req ChargeRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
			return
		}
		c := store.Charge(req.OrderID, req.AmountCents, req.CardToken)
		resp := ChargeResponse{
			Status:    statusText(int32(c.Status)),
			ChargeID:  c.ChargeID,
			OrderID:   c.OrderID,
			Amount:    c.AmountCents,
			CardToken: c.CardToken,
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(resp)
	}
}

// HealthzHandler handles GET /healthz.
func HealthzHandler(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write([]byte(`{"status":"ok"}`))
}

// ReadyzHandler handles GET /readyz.
func ReadyzHandler(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write([]byte(`{"status":"ok"}`))
}
