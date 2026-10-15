package gateway

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func response(body string) *http.Response {
	return &http.Response{
		StatusCode: http.StatusOK,
		Header:     http.Header{"Content-Type": []string{"application/json"}},
		Body:       io.NopCloser(strings.NewReader(body)),
	}
}

func TestObservabilityStoreQueriesAllowListedPrometheusMetric(t *testing.T) {
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Path != "/api/v1/query_range" {
			t.Fatalf("unexpected path %s", request.URL.Path)
		}
		if !strings.Contains(request.URL.Query().Get("query"), `service="payment-service"`) {
			t.Fatalf("service matcher missing from %s", request.URL.RawQuery)
		}
		return response(`{
			"status":"success",
			"data":{"resultType":"matrix","result":[{"metric":{},"values":[
				[1784786400,"643"],[1784786460,"2840"]
			]}]}
		}`), nil
	})}

	store := NewObservabilityStore("http://prometheus", "http://loki", "http://tempo", client, time.Now)
	result, err := store.QueryMetrics(context.Background(), MetricQuery{
		Service:  "payment-service",
		Metric:   "request_latency_p95",
		Start:    time.Date(2026, 7, 23, 6, 0, 0, 0, time.UTC),
		End:      time.Date(2026, 7, 23, 6, 30, 0, 0, time.UTC),
		StepSecs: 60,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Points) != 2 || result.Points[1].Value != 2840 {
		t.Fatalf("unexpected points: %#v", result.Points)
	}
	if result.DataOrigin != "prometheus_api" {
		t.Fatalf("live query provenance missing, got %q", result.DataOrigin)
	}
}

func TestObservabilityStoreRejectsUnlistedMetric(t *testing.T) {
	store := NewObservabilityStore("http://prometheus", "http://loki", "http://tempo", http.DefaultClient, time.Now)
	_, err := store.QueryMetrics(context.Background(), MetricQuery{
		Service: "payment-service",
		Metric:  `up or vector(1)`,
	})
	if err == nil || !strings.Contains(err.Error(), "not allow-listed") {
		t.Fatalf("expected allow-list error, got %v", err)
	}
}

func TestObservabilityStoreDiscardsNonFiniteSamplesWithoutLosingValidPoints(t *testing.T) {
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		return response(`{"status":"success","data":{"result":[{"values":[
			[1784786400,"NaN"],[1784786415,"+Inf"],[1784786430,"-Inf"],
			[1784786445,"invalid"],[1784786460,"475"],[1784786475,"0"]
		]}]}}`), nil
	})}
	store := NewObservabilityStore("http://prometheus", "http://loki", "http://tempo", client, time.Now)
	result, err := store.QueryMetrics(context.Background(), MetricQuery{Service: "payment-service", Metric: "request_latency_p95"})
	if err != nil || len(result.Points) != 2 || result.Points[0].Value != 475 || result.Points[1].Value != 0 {
		t.Fatalf("non-finite values must be omitted, not zero-filled: %#v, %v", result.Points, err)
	}
	if _, err := json.Marshal(result); err != nil {
		t.Fatalf("finite sample response must remain JSON encodable: %v", err)
	}
}

func TestObservabilityStoreParsesLokiJSONLogs(t *testing.T) {
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Path != "/loki/api/v1/query_range" {
			t.Fatalf("unexpected path %s", request.URL.Path)
		}
		return response(`{
			"status":"success",
			"data":{"resultType":"streams","result":[{
				"stream":{"service":"payment-service"},
				"values":[["1784786460000000000","{\"level\":\"error\",\"message\":\"database connection acquisition timed out\",\"pool\":\"ledger-primary\",\"active\":50,\"max\":50,\"trace_id\":\"trace-001\",\"span_id\":\"1111111111111111\",\"request_id\":\"client-001\"}"]]
			}]}
		}`), nil
	})}

	store := NewObservabilityStore("http://prometheus", "http://loki", "http://tempo", client, time.Now)
	events, err := store.SearchLogs(context.Background(), LogSearch{
		Service: "payment-service",
		Query:   "acquisition timed out",
		Start:   time.Date(2026, 7, 23, 6, 0, 0, 0, time.UTC),
		End:     time.Date(2026, 7, 23, 6, 30, 0, 0, time.UTC),
		Limit:   10,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 1 || events[0].Fields["active"] != "50" || events[0].Fields["span_id"] != "1111111111111111" || events[0].Fields["request_id"] != "client-001" {
		t.Fatalf("unexpected events: %#v", events)
	}
}

func TestObservabilityStoreSearchesTempoWithBoundedTraceQL(t *testing.T) {
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Path != "/api/search" {
			t.Fatalf("unexpected path %s", request.URL.Path)
		}
		if request.URL.Query().Get("q") != `{ resource.service.name = "payment-service" } with (most_recent=true)` {
			t.Fatalf("bounded newest-first service query missing from %s", request.URL.RawQuery)
		}
		if request.URL.Query().Get("limit") != "10" || request.URL.Query().Get("start") != "1784786400" || request.URL.Query().Get("end") != "1784788200" {
			t.Fatalf("trace count/time bounds changed: %s", request.URL.RawQuery)
		}
		return response(`{"traces":[{
			"traceID":"4f3a",
			"rootServiceName":"checkout-api",
			"rootTraceName":"POST /v1/checkout",
			"startTimeUnixNano":"1784786460000000000",
			"durationMs":2840.5
		}]}`), nil
	})}
	store := NewObservabilityStore(
		"http://prometheus",
		"http://loki",
		"http://tempo",
		client,
		time.Now,
	)
	traces, err := store.SearchTraces(context.Background(), TraceSearch{
		Service: "payment-service",
		Start:   time.Date(2026, 7, 23, 6, 0, 0, 0, time.UTC),
		End:     time.Date(2026, 7, 23, 6, 30, 0, 0, time.UTC),
		Limit:   10,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(traces) != 1 || traces[0].TraceID != "00000000000000000000000000004f3a" {
		t.Fatalf("unexpected traces: %#v", traces)
	}
}
