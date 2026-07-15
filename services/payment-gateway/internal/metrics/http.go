package metrics

import (
	"fmt"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"
)

var explicitBoundaries = [...]float64{
	0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1, 2, 2.5, 5, 10,
}

type seriesKey struct {
	statusCode string
	route      string
}

type histogram struct {
	buckets [len(explicitBoundaries)]uint64
	count   uint64
	sum     float64
}

type Registry struct {
	mu          sync.RWMutex
	serviceName string
	series      map[seriesKey]histogram
}

func NewRegistry(serviceName string) *Registry {
	return &Registry{
		serviceName: serviceName,
		series:      make(map[seriesKey]histogram),
	}
}

func (r *Registry) Observe(statusCode int, route string, duration time.Duration) {
	if route == "" {
		route = "unmatched"
	}
	key := seriesKey{statusCode: strconv.Itoa(statusCode), route: route}
	seconds := duration.Seconds()
	r.mu.Lock()
	defer r.mu.Unlock()
	value := r.series[key]
	for index, boundary := range explicitBoundaries {
		if seconds <= boundary {
			value.buckets[index]++
		}
	}
	value.count++
	value.sum += seconds
	r.series[key] = value
}

func (r *Registry) Handler() http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
		_, _ = response.Write([]byte(r.render()))
	})
}

func (r *Registry) Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if isExcluded(request.URL.Path) {
			next.ServeHTTP(response, request)
			return
		}
		wrapped := &statusWriter{ResponseWriter: response, statusCode: http.StatusOK}
		started := time.Now()
		next.ServeHTTP(wrapped, request)
		r.Observe(wrapped.statusCode, chi.RouteContext(request.Context()).RoutePattern(), time.Since(started))
	})
}

func (r *Registry) render() string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	keys := make([]seriesKey, 0, len(r.series))
	for key := range r.series {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(left, right int) bool {
		if keys[left].route == keys[right].route {
			return keys[left].statusCode < keys[right].statusCode
		}
		return keys[left].route < keys[right].route
	})
	var output strings.Builder
	output.WriteString("# HELP http_server_request_duration_seconds Inbound HTTP request duration\n")
	output.WriteString("# TYPE http_server_request_duration_seconds histogram\n")
	for _, key := range keys {
		value := r.series[key]
		labels := fmt.Sprintf(
			`http_response_status_code="%s",http_route="%s",service_name="%s"`,
			escapeLabel(key.statusCode), escapeLabel(key.route), escapeLabel(r.serviceName),
		)
		for index, boundary := range explicitBoundaries {
			fmt.Fprintf(&output, "http_server_request_duration_seconds_bucket{%s,le=\"%s\"} %d\n", labels, strconv.FormatFloat(boundary, 'g', -1, 64), value.buckets[index])
		}
		fmt.Fprintf(&output, "http_server_request_duration_seconds_bucket{%s,le=\"+Inf\"} %d\n", labels, value.count)
		fmt.Fprintf(&output, "http_server_request_duration_seconds_sum{%s} %s\n", labels, strconv.FormatFloat(value.sum, 'g', -1, 64))
		fmt.Fprintf(&output, "http_server_request_duration_seconds_count{%s} %d\n", labels, value.count)
	}
	return output.String()
}

type statusWriter struct {
	http.ResponseWriter
	statusCode  int
	wroteHeader bool
}

func (w *statusWriter) WriteHeader(statusCode int) {
	if w.wroteHeader {
		return
	}
	w.wroteHeader = true
	w.statusCode = statusCode
	w.ResponseWriter.WriteHeader(statusCode)
}

func (w *statusWriter) Write(body []byte) (int, error) {
	if !w.wroteHeader {
		w.WriteHeader(http.StatusOK)
	}
	return w.ResponseWriter.Write(body)
}

func isExcluded(path string) bool {
	return path == "/healthz" || path == "/readyz" || path == "/metrics"
}

func escapeLabel(value string) string {
	value = strings.ReplaceAll(value, `\`, `\\`)
	value = strings.ReplaceAll(value, "\n", `\n`)
	return strings.ReplaceAll(value, `"`, `\"`)
}
