package metrics_test

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/example/payment-gateway/internal/metrics"
)

func Test_Registry_exposes_cumulative_histogram_with_canonical_labels(t *testing.T) {
	t.Parallel()

	registry := metrics.NewRegistry("payment-gateway")
	registry.Observe(http.StatusOK, "/charge", 200*time.Millisecond)
	response := httptest.NewRecorder()
	registry.Handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/metrics", nil))

	require.Equal(t, http.StatusOK, response.Code)
	body := response.Body.String()
	labels := `http_response_status_code="200",http_route="/charge",service_name="payment-gateway"`
	assert.Contains(t, body, `http_server_request_duration_seconds_count{`+labels+`} 1`)
	assert.Contains(t, body, `http_server_request_duration_seconds_sum{`+labels+`} 0.2`)
	assert.Contains(t, body, `http_server_request_duration_seconds_bucket{`+labels+`,le="0.2"} 1`)
	assert.Contains(t, body, `http_server_request_duration_seconds_bucket{`+labels+`,le="1"} 1`)
	assert.Contains(t, body, `http_server_request_duration_seconds_bucket{`+labels+`,le="+Inf"} 1`)
}

func Test_Middleware_uses_route_template_and_excludes_probes(t *testing.T) {
	t.Parallel()

	registry := metrics.NewRegistry("payment-gateway")
	router := chi.NewRouter()
	router.Use(registry.Middleware)
	router.Post("/charge/{order_id}", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusCreated)
	})
	router.Get("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	router.Get("/metrics", registry.Handler().ServeHTTP)

	router.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodPost, "/charge/secret-id", nil))
	router.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/healthz", nil))
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/metrics", nil))

	body := response.Body.String()
	assert.Contains(t, body, `http_route="/charge/{order_id}"`)
	assert.NotContains(t, body, "secret-id")
	assert.NotContains(t, body, `http_route="/healthz"`)
	assert.NotContains(t, body, `http_route="/metrics"`)
}

func Test_Middleware_records_the_first_response_status(t *testing.T) {
	t.Parallel()

	registry := metrics.NewRegistry("payment-gateway")
	router := chi.NewRouter()
	router.Use(registry.Middleware)
	router.Get("/status", func(response http.ResponseWriter, _ *http.Request) {
		response.WriteHeader(http.StatusCreated)
		response.WriteHeader(http.StatusInternalServerError)
	})

	router.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/status", nil))
	response := httptest.NewRecorder()
	registry.Handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/metrics", nil))

	assert.Contains(t, response.Body.String(), `http_response_status_code="201"`)
	assert.NotContains(t, response.Body.String(), `http_response_status_code="500"`)
}

func Test_Handler_escapes_label_values_and_sets_prometheus_content_type(t *testing.T) {
	t.Parallel()

	registry := metrics.NewRegistry("payment\"gateway\\line\nnext")
	registry.Observe(http.StatusTeapot, "/quoted\"route", time.Second)
	response := httptest.NewRecorder()

	registry.Handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/metrics", nil))

	assert.True(t, strings.HasPrefix(response.Header().Get("Content-Type"), "text/plain"))
	assert.Contains(t, response.Body.String(), `service_name="payment\"gateway\\line\nnext"`)
	assert.Contains(t, response.Body.String(), `http_route="/quoted\"route"`)
}
