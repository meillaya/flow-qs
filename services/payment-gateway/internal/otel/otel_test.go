package otel_test

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"

	pgotel "github.com/example/payment-gateway/internal/otel"
)

func TestInit_SetsTracerProvider(t *testing.T) {
	shutdown, err := pgotel.Init(context.Background(), "payment-gateway-test")
	require.NoError(t, err)
	defer func() {
		ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
		defer cancel()
		_ = shutdown(ctx)
	}()

	provider := otel.GetTracerProvider()
	assert.NotNil(t, provider)
}

func TestInit_SetsPropagators(t *testing.T) {
	shutdown, err := pgotel.Init(context.Background(), "payment-gateway-test")
	require.NoError(t, err)
	defer func() {
		// Shutdown may time out because there is no OTLP collector in unit tests.
		ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
		defer cancel()
		_ = shutdown(ctx)
	}()

	prop := otel.GetTextMapPropagator()
	require.NotNil(t, prop)

	ctx, span := otel.Tracer("test").Start(context.Background(), "test-span")
	defer span.End()

	carrier := propagation.MapCarrier{}
	prop.Inject(ctx, carrier)
	assert.Contains(t, carrier, "traceparent")
}
