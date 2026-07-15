package main

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
)

type requestLogEntry struct {
	Message string `json:"msg"`
	Method  string `json:"method"`
	Path    string `json:"path"`
	Status  int    `json:"status"`
}

func Test_RequestLogger_records_completed_application_requests(t *testing.T) {
	var output bytes.Buffer
	logger := slog.New(slog.NewJSONHandler(&output, nil))
	handler := requestLogger(logger)(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.WriteHeader(http.StatusAccepted)
	}))

	request := httptest.NewRequest(http.MethodPost, "/charge", nil)
	handler.ServeHTTP(httptest.NewRecorder(), request)

	var entry requestLogEntry
	if err := json.Unmarshal(output.Bytes(), &entry); err != nil {
		t.Fatalf("decode request log: %v", err)
	}
	if entry.Message != "http request" || entry.Method != http.MethodPost || entry.Path != "/charge" || entry.Status != http.StatusAccepted {
		t.Fatalf("unexpected request log: %+v", entry)
	}
}

func Test_RequestLogger_omits_probe_traffic(t *testing.T) {
	var output bytes.Buffer
	logger := slog.New(slog.NewJSONHandler(&output, nil))
	handler := requestLogger(logger)(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.WriteHeader(http.StatusOK)
	}))

	for _, path := range []string{"/healthz", "/readyz", "/metrics"} {
		handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, path, nil))
	}

	if output.Len() != 0 {
		t.Fatalf("probe traffic produced request logs: %s", output.String())
	}
}
