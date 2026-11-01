package handlers_test

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/meillaya/flow-qs/services/payment-gateway/internal/handlers"
	"github.com/meillaya/flow-qs/services/payment-gateway/internal/payment"
)

func Test_ChargeHandler_redacts_card_token_when_request_is_valid(t *testing.T) {
	t.Parallel()

	store := payment.NewStore()
	request := httptest.NewRequest(http.MethodPost, "/charge", strings.NewReader(
		`{"order_id":"o-1","amount_cents":5000,"card_token":"secret"}`,
	))
	response := httptest.NewRecorder()

	handlers.ChargeHandler(store).ServeHTTP(response, request)

	require.Equal(t, http.StatusOK, response.Code)
	var body map[string]json.RawMessage
	require.NoError(t, json.Unmarshal(response.Body.Bytes(), &body))
	assert.Equal(t, []string{"amount_cents", "charge_id", "order_id", "status"}, sortedKeys(body))
	assert.NotContains(t, response.Body.String(), "secret")
	stored := store.Get(stringValue(t, body["charge_id"]))
	require.NotNil(t, stored)
	assert.NotContains(t, fmt.Sprintf("%+v", stored), "secret")
}

func Test_ChargeHandler_returns_bad_request_when_charge_is_invalid(t *testing.T) {
	t.Parallel()

	tests := map[string]string{
		"blank order":     `{"order_id":" ","amount_cents":5000,"card_token":"token"}`,
		"zero amount":     `{"order_id":"o-1","amount_cents":0,"card_token":"token"}`,
		"negative amount": `{"order_id":"o-1","amount_cents":-1,"card_token":"token"}`,
		"blank token":     `{"order_id":"o-1","amount_cents":5000,"card_token":" "}`,
	}
	for name, body := range tests {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			store := payment.NewStore()
			request := httptest.NewRequest(http.MethodPost, "/charge", strings.NewReader(body))
			response := httptest.NewRecorder()

			handlers.ChargeHandler(store).ServeHTTP(response, request)

			assert.Equal(t, http.StatusBadRequest, response.Code)
			assert.JSONEq(t, `{"error":"invalid charge"}`, response.Body.String())
			assert.Zero(t, store.Len())
		})
	}
}

func Test_ChargeHandler_rejects_unknown_or_trailing_JSON(t *testing.T) {
	t.Parallel()

	tests := map[string]string{
		"unknown field":   `{"order_id":"o-1","amount_cents":5000,"card_token":"token","cvv":"123"}`,
		"trailing object": `{"order_id":"o-1","amount_cents":5000,"card_token":"token"}{}`,
	}
	for name, body := range tests {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			store := payment.NewStore()
			request := httptest.NewRequest(http.MethodPost, "/charge", strings.NewReader(body))
			response := httptest.NewRecorder()

			handlers.ChargeHandler(store).ServeHTTP(response, request)

			assert.Equal(t, http.StatusBadRequest, response.Code)
			assert.JSONEq(t, `{"error":"invalid charge"}`, response.Body.String())
			assert.Zero(t, store.Len())
		})
	}
}

func sortedKeys(values map[string]json.RawMessage) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	slices.Sort(keys)
	return keys
}

func stringValue(t *testing.T, raw json.RawMessage) string {
	t.Helper()
	var value string
	require.NoError(t, json.Unmarshal(raw, &value))
	return value
}
