package gateway

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func testServer() *Server {
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	return NewServer(NewFixtureStore(time.Now), logger, "test-token")
}

func TestDependencyCatalogProvenance(t *testing.T) {
	request := httptest.NewRequest(http.MethodGet, "/v1/services/payment-service/dependencies", nil)
	request.Header.Set("Authorization", "Bearer test-token")
	response := httptest.NewRecorder()
	testServer().Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", response.Code)
	}
	var result map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result["topology_kind"] != "static_fixture_catalog" {
		t.Fatal("dependency catalog must not be presented as live trace evidence")
	}
}

func TestDeploymentCatalogProvenance(t *testing.T) {
	request := httptest.NewRequest(http.MethodGet, "/v1/services/payment-service/deployments", nil)
	request.Header.Set("Authorization", "Bearer test-token")
	response := httptest.NewRecorder()
	testServer().Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", response.Code)
	}
	var result map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result["data_origin"] != "synthetic" || result["source"] != "synthetic-deployment-catalog" {
		t.Fatal("fixture deployment catalog must not be presented as live Kubernetes metadata")
	}
}

func TestMetricsRequiresAuthentication(t *testing.T) {
	request := httptest.NewRequest(http.MethodPost, "/v1/metrics/query", nil)
	response := httptest.NewRecorder()

	testServer().Handler().ServeHTTP(response, request)

	if response.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", response.Code)
	}
}

func TestGatewayRateLimitMatchesParallelIncidentQueryBudget(t *testing.T) {
	server := testServer()
	for index := 0; index < defaultRequestsPerMinute; index++ {
		request := httptest.NewRequest(http.MethodGet, "/health", nil)
		request.RemoteAddr = "192.0.2.10:1234"
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, request)
		if response.Code != http.StatusOK {
			t.Fatalf("request %d unexpectedly returned %d", index+1, response.Code)
		}
	}
	request := httptest.NewRequest(http.MethodGet, "/health", nil)
	request.RemoteAddr = "192.0.2.10:1234"
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusTooManyRequests {
		t.Fatalf("expected 429 after budget, got %d", response.Code)
	}
}

func TestMetricQueryRejectsUnknownFields(t *testing.T) {
	body := []byte(`{
		"service":"payment-service",
		"metric":"request_latency_p95",
		"start":"2026-07-23T06:00:00Z",
		"end":"2026-07-23T06:30:00Z",
		"step_seconds":60,
		"unsafe":"value"
	}`)
	request := httptest.NewRequest(http.MethodPost, "/v1/metrics/query", bytes.NewReader(body))
	request.Header.Set("Authorization", "Bearer test-token")
	response := httptest.NewRecorder()

	testServer().Handler().ServeHTTP(response, request)

	if response.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", response.Code)
	}
}

func TestMetricQueryReturnsFixtureSeries(t *testing.T) {
	payload := MetricQuery{
		Service:  "payment-service",
		Metric:   "request_latency_p95",
		Start:    time.Now().Add(-30 * time.Minute).UTC(),
		End:      time.Now().UTC(),
		StepSecs: 60,
	}
	body, _ := json.Marshal(payload)
	request := httptest.NewRequest(http.MethodPost, "/v1/metrics/query", bytes.NewReader(body))
	request.Header.Set("Authorization", "Bearer test-token")
	response := httptest.NewRecorder()

	testServer().Handler().ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", response.Code, response.Body.String())
	}
	var result MetricResult
	if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if len(result.Points) != 16 {
		t.Fatalf("expected 16 points, got %d", len(result.Points))
	}
	if result.DataOrigin != "synthetic" {
		t.Fatalf("fixture provenance must stay synthetic, got %q", result.DataOrigin)
	}
}

func TestFixtureMetricsCoverEveryIncidentFamily(t *testing.T) {
	store := NewFixtureStore(time.Now)
	expectedUnits := map[string]string{
		"request_latency_p95":  "milliseconds",
		"db_pool_active_ratio": "ratio",
		"request_error_rate":   "ratio",
		"cpu_utilization":      "ratio",
		"memory_working_set":   "bytes",
		"kafka_consumer_lag":   "messages",
		"retry_rate":           "retries_per_second",
		"cache_miss_rate":      "misses_per_second",
	}
	for metric, expectedUnit := range expectedUnits {
		t.Run(metric, func(t *testing.T) {
			result, err := store.QueryMetrics(context.Background(), MetricQuery{
				Service:  "payment-service",
				Metric:   metric,
				Start:    time.Now().Add(-30 * time.Minute).UTC(),
				End:      time.Now().UTC(),
				StepSecs: 60,
			})
			if err != nil {
				t.Fatal(err)
			}
			if result.Unit != expectedUnit {
				t.Fatalf("expected unit %s, got %s", expectedUnit, result.Unit)
			}
			if len(result.Points) != 16 {
				t.Fatalf("expected fixture points for %s", metric)
			}
		})
	}
}

func TestTraceSearchReturnsBoundedFixtureTraces(t *testing.T) {
	payload := TraceSearch{
		Service: "payment-service",
		Start:   time.Now().Add(-30 * time.Minute).UTC(),
		End:     time.Now().UTC(),
		Limit:   2,
	}
	body, _ := json.Marshal(payload)
	request := httptest.NewRequest(http.MethodPost, "/v1/traces/search", bytes.NewReader(body))
	request.Header.Set("Authorization", "Bearer test-token")
	response := httptest.NewRecorder()

	testServer().Handler().ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", response.Code, response.Body.String())
	}
	var result struct {
		Traces []TraceSummary `json:"traces"`
		Count  int            `json:"count"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result.Count != 2 || len(result.Traces) != 2 {
		t.Fatalf("expected two traces, got %#v", result)
	}
}
