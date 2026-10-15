package gateway

import (
	"context"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"
)

const topologyTrace = "11111111111111111111111111111111"

func TestTempoSearchTraceIDNormalizationIsBoundarySpecific(t *testing.T) {
	for _, test := range []struct{ input, want string }{
		{"480e4b8cae567dc780306c46ad07035", "0480e4b8cae567dc780306c46ad07035"},
		{"F", "0000000000000000000000000000000f"},
		{topologyTrace, topologyTrace},
		{"", ""}, {"0", ""}, {strings.Repeat("0", 32), ""},
		{strings.Repeat("a", 33), ""}, {"../traces", ""}, {"+abcd", ""},
		{"BIDkuMrlZ9x4AwbEatBwNQ==", ""},
	} {
		if got := canonicalTempoSearchID(test.input); got != test.want {
			t.Errorf("Tempo ID %q: got %q, want %q", test.input, got, test.want)
		}
	}
	if canonicalID("480e4b8cae567dc780306c46ad07035", 16) != "" {
		t.Fatal("normalizing Tempo search must not loosen OTLP byte-width validation")
	}
}

func TestObservedDependenciesReadLeadingZeroTraceAndDeduplicateSearchForms(t *testing.T) {
	const short = "480e4b8cae567dc780306c46ad07035"
	const full = "0480e4b8cae567dc780306c46ad07035"
	reads := 0
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Path == "/api/search" {
			if request.URL.Query().Get("q") != `{ resource.service.name = "payment-service" } with (most_recent=true)` || request.URL.Query().Get("limit") != "8" {
				t.Fatalf("observed parentage must use bounded newest-first search: %s", request.URL.RawQuery)
			}
			stamp := strconv.FormatInt(topologyQuery().End.Add(-time.Minute).UnixNano(), 10)
			return response(fmt.Sprintf(`{"traces":[{"traceID":%q,"startTimeUnixNano":%q},{"traceID":%q,"startTimeUnixNano":%q}]}`, short, stamp, full, stamp)), nil
		}
		reads++
		if request.URL.Path != "/api/traces/"+full {
			t.Fatalf("expected padded trace lookup: %s", request.URL.Path)
		}
		parent := spanFixture("checkout-api", "1111111111111111", "", 3)
		child := spanFixture("payment-service", "2222222222222222", "1111111111111111", 2)
		for _, resource := range []map[string]any{parent, child} {
			fixtureSpan(resource)["traceId"] = full
		}
		return response(string(fixtureJSON(parent, child))), nil
	})}
	store := NewObservabilityStore("http://prometheus", "http://loki", "http://tempo", client, time.Now)
	result, err := store.ObservedDependencies(context.Background(), topologyQuery())
	if err != nil || reads != 1 || result.TracesRead != 1 || len(result.Calls) != 1 || result.Calls[0].TraceID != full {
		t.Fatalf("leading-zero trace parentage lost: %v %#v (%d reads)", err, result, reads)
	}
}

func topologyQuery() TraceSearch {
	end := time.Date(2026, 10, 2, 16, 0, 0, 0, time.UTC)
	return TraceSearch{Service: "payment-service", Start: end.Add(-30 * time.Minute), End: end, Limit: 8}
}

func spanFixture(service, id, parent string, kind any) map[string]any {
	query := topologyQuery()
	return map[string]any{
		"resource": map[string]any{"attributes": []any{map[string]any{"key": "service.name", "value": map[string]any{"stringValue": service}}}},
		"scopeSpans": []any{map[string]any{"spans": []any{map[string]any{
			"traceId": topologyTrace, "spanId": id, "parentSpanId": parent, "kind": kind,
			"startTimeUnixNano": strconv.FormatInt(query.End.Add(-time.Minute).UnixNano(), 10),
			"endTimeUnixNano":   strconv.FormatInt(query.End.Add(-time.Second).UnixNano(), 10),
		}}}},
	}
}

func fixtureSpan(resource map[string]any) map[string]any {
	return resource["scopeSpans"].([]any)[0].(map[string]any)["spans"].([]any)[0].(map[string]any)
}

func fixtureJSON(resources ...map[string]any) json.RawMessage {
	bytes, _ := json.Marshal(map[string]any{"resourceSpans": resources})
	return bytes
}

func TestTraceCallsRequireExactClientServerParentage(t *testing.T) {
	client := spanFixture("checkout-api", "1111111111111111", "", "SPAN_KIND_CLIENT")
	server := spanFixture("payment-service", "2222222222222222", "1111111111111111", 2)
	calls, err := extractTraceCalls(fixtureJSON(server, client), topologyTrace, topologyQuery())
	if err != nil || len(calls) != 1 {
		t.Fatalf("expected one observed call: %v %#v", err, calls)
	}
	call := calls[0]
	if call.Source != "checkout-api" || call.Target != "payment-service" || call.ClientSpanID != "1111111111111111" || call.ServerSpanID != "2222222222222222" {
		t.Fatalf("incorrect observed parentage: %#v", call)
	}
	encoded, _ := json.Marshal(call)
	if strings.Contains(string(encoded), "error_rate") || strings.Contains(string(encoded), "p95") {
		t.Fatal("observed calls must not invent health metrics")
	}
}

func TestTraceCallsSupportTempoLegacyBatchesAndBase64IDs(t *testing.T) {
	client := spanFixture("payment-service", "1111111111111111", "", 3)
	server := spanFixture("ledger-service", "2222222222222222", "1111111111111111", "SPAN_KIND_SERVER")
	for _, resource := range []map[string]any{client, server} {
		span := fixtureSpan(resource)
		for _, key := range []string{"traceId", "spanId", "parentSpanId"} {
			decoded, _ := hex.DecodeString(span[key].(string))
			span[key] = base64.StdEncoding.EncodeToString(decoded)
		}
		resource["instrumentationLibrarySpans"] = resource["scopeSpans"]
		delete(resource, "scopeSpans")
	}
	raw, _ := json.Marshal(map[string]any{"batches": []any{client, server}})
	calls, err := extractTraceCalls(raw, topologyTrace, topologyQuery())
	if err != nil || len(calls) != 1 || calls[0].Target != "ledger-service" {
		t.Fatalf("legacy/protobuf JSON parentage failed: %v %#v", err, calls)
	}
}

func TestTraceCallsDiscardUnsupportedOrAmbiguousRelationships(t *testing.T) {
	for _, testCase := range []string{"orphan", "same-service", "internal", "consumer", "cross-trace", "duplicate-parent", "duplicate-child", "no-service", "old", "bad-id", "cycle", "unrelated", "bad-time"} {
		t.Run(testCase, func(t *testing.T) {
			client := spanFixture("checkout-api", "1111111111111111", "", 3)
			server := spanFixture("payment-service", "2222222222222222", "1111111111111111", 2)
			resources := []map[string]any{client, server}
			switch testCase {
			case "orphan":
				fixtureSpan(server)["parentSpanId"] = "3333333333333333"
			case "same-service":
				resources[0] = spanFixture("payment-service", "1111111111111111", "", 3)
			case "internal":
				fixtureSpan(client)["kind"] = 1
			case "consumer":
				fixtureSpan(server)["kind"] = 5
			case "cross-trace":
				fixtureSpan(client)["traceId"] = "33333333333333333333333333333333"
			case "duplicate-parent":
				resources = append(resources, client)
			case "duplicate-child":
				resources = append(resources, server)
			case "no-service":
				resources[0] = spanFixture("", "1111111111111111", "", 3)
			case "old":
				fixtureSpan(server)["startTimeUnixNano"] = "1"
				fixtureSpan(server)["endTimeUnixNano"] = "2"
			case "bad-id":
				fixtureSpan(client)["spanId"] = "../private"
			case "cycle":
				fixtureSpan(client)["parentSpanId"] = "2222222222222222"
			case "unrelated":
				resources[1] = spanFixture("risk-engine", "2222222222222222", "1111111111111111", 2)
			case "bad-time":
				fixtureSpan(client)["startTimeUnixNano"] = "not-a-number"
			}
			calls, err := extractTraceCalls(fixtureJSON(resources...), topologyTrace, topologyQuery())
			if err != nil || len(calls) != 0 {
				t.Fatalf("invalid relationship became an edge: %v %#v", err, calls)
			}
		})
	}
}

func TestTraceDocumentBudgetsAndFormatsFailClosed(t *testing.T) {
	resources := make([]map[string]any, 2049)
	for index := range resources {
		resources[index] = spanFixture("payment-service", "1111111111111111", "", 3)
	}
	for _, raw := range []json.RawMessage{fixtureJSON(resources...), json.RawMessage(`{}`), json.RawMessage(`{"resourceSpans":[],"batches":[]}`), json.RawMessage(`broken`)} {
		if _, err := extractTraceCalls(raw, topologyTrace, topologyQuery()); err == nil {
			t.Fatal("unsupported or oversized document should be rejected")
		}
	}
	if canonicalID("0000000000000000", 8) != "" || canonicalID("../unsafe", 16) != "" {
		t.Fatal("invalid trace/span identifiers accepted")
	}
}

func TestObservedDependencyFetchBudgetPartialFailureAndNoCatalogFallback(t *testing.T) {
	fetched := 0
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.Method != http.MethodGet || request.URL.Query().Get("start") == "" || request.URL.Query().Get("end") == "" {
			t.Fatal("expected bounded read-only request")
		}
		if request.URL.Path == "/api/search" {
			if request.URL.Query().Get("limit") != "8" {
				t.Fatal("unexpected trace search budget")
			}
			traces := []any{}
			for index := 1; index <= 12; index++ {
				traces = append(traces, map[string]any{"traceID": fmt.Sprintf("%032x", index), "startTimeUnixNano": strconv.FormatInt(topologyQuery().End.Add(-time.Minute).UnixNano(), 10)})
			}
			bytes, _ := json.Marshal(map[string]any{"traces": traces})
			return response(string(bytes)), nil
		}
		fetched++
		if fetched == 1 {
			return nil, fmt.Errorf("private provider detail")
		}
		return response(`{"resourceSpans":[]}`), nil
	})}
	store := NewObservabilityStore("http://prometheus", "http://loki", "http://tempo", client, time.Now)
	result, err := store.ObservedDependencies(context.Background(), topologyQuery())
	if err != nil || fetched != 8 || result.TracesRead != 7 || result.TracesFailed != 1 || len(result.Calls) != 0 {
		t.Fatalf("incorrect partial observation: %v %#v, fetched %d", err, result, fetched)
	}
	if result.Coverage != "partial_sample_not_exhaustive" {
		t.Fatal("partial trace reads cannot imply exhaustive topology")
	}
}

func TestObservedDependencyEndpointNeverUsesFixtureCatalog(t *testing.T) {
	request := httptest.NewRequest(http.MethodGet, "/v1/services/payment-service/observed-dependencies", nil)
	request.Header.Set("Authorization", "Bearer test-token")
	recorder := httptest.NewRecorder()
	testServer().Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusNotImplemented || strings.Contains(recorder.Body.String(), "checkout-api") {
		t.Fatal("fixture backend must not masquerade as observed parentage")
	}
}

func TestObservedDependencyFetchRetainsInspectableParentage(t *testing.T) {
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		deadline, exists := request.Context().Deadline()
		if !exists || time.Until(deadline) > 5*time.Second {
			t.Fatal("dependency reads require an overall deadline")
		}
		if request.URL.Path == "/api/search" {
			return response(fmt.Sprintf(`{"traces":[{"traceID":%q,"startTimeUnixNano":%q}]}`, topologyTrace, strconv.FormatInt(topologyQuery().End.Add(-time.Minute).UnixNano(), 10))), nil
		}
		if request.URL.Path != "/api/traces/"+topologyTrace {
			t.Fatalf("trace id must be canonical and path-bounded: %s", request.URL.Path)
		}
		return response(string(fixtureJSON(spanFixture("payment-service", "1111111111111111", "", 3), spanFixture("ledger-service", "2222222222222222", "1111111111111111", 2)))), nil
	})}
	store := NewObservabilityStore("http://prometheus", "http://loki", "http://tempo", client, time.Now)
	result, err := store.ObservedDependencies(context.Background(), topologyQuery())
	if err != nil || result.TracesRead != 1 || len(result.Calls) != 1 || result.Calls[0].Target != "ledger-service" {
		t.Fatalf("lost observed parentage: %v %#v", err, result)
	}
}

func TestObservedDependencyCancellationIsNotEmptyTopology(t *testing.T) {
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		<-request.Context().Done()
		return nil, request.Context().Err()
	})}
	store := NewObservabilityStore("http://prometheus", "http://loki", "http://tempo", client, time.Now)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := store.ObservedDependencies(ctx, topologyQuery()); err == nil {
		t.Fatal("cancelled trace search must not become an empty successful topology")
	}
}

func TestObservedDependencyEndpointPreservesAuthenticationAndRejectsOverrides(t *testing.T) {
	for _, testCase := range []struct {
		query, token string
		status       int
	}{
		{"", "", http.StatusUnauthorized}, {"?start=0", "test-token", http.StatusUnprocessableEntity},
	} {
		request := httptest.NewRequest(http.MethodGet, "/v1/services/payment-service/observed-dependencies"+testCase.query, nil)
		request.Header.Set("Authorization", "Bearer "+testCase.token)
		recorder := httptest.NewRecorder()
		testServer().Handler().ServeHTTP(recorder, request)
		if recorder.Code != testCase.status {
			t.Fatalf("expected %d, got %d", testCase.status, recorder.Code)
		}
	}
}

func TestObservedDependencyEndpointRedactsBackendFailures(t *testing.T) {
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) { return nil, fmt.Errorf("private provider token") })}
	store := NewObservabilityStore("http://prometheus", "http://loki", "http://tempo", client, time.Now)
	server := NewServer(store, slog.New(slog.NewTextHandler(io.Discard, nil)), "test-token")
	request := httptest.NewRequest(http.MethodGet, "/v1/services/payment-service/observed-dependencies", nil)
	request.Header.Set("Authorization", "Bearer test-token")
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusBadGateway || strings.Contains(recorder.Body.String(), "private") {
		t.Fatalf("unsafe provider failure: %s", recorder.Body.String())
	}
}

func TestObservedDependencyEndpointReturnsCallsNotHealthOrCausality(t *testing.T) {
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		now := time.Now().UTC().Add(-time.Minute)
		if request.URL.Path == "/api/search" {
			return response(fmt.Sprintf(`{"traces":[{"traceID":%q,"startTimeUnixNano":%q}]}`, topologyTrace, strconv.FormatInt(now.UnixNano(), 10))), nil
		}
		caller := spanFixture("payment-service", "1111111111111111", "", 3)
		callee := spanFixture("ledger-service", "2222222222222222", "1111111111111111", 2)
		for _, resource := range []map[string]any{caller, callee} {
			fixtureSpan(resource)["startTimeUnixNano"] = strconv.FormatInt(now.UnixNano(), 10)
			fixtureSpan(resource)["endTimeUnixNano"] = strconv.FormatInt(now.Add(time.Second).UnixNano(), 10)
		}
		return response(string(fixtureJSON(caller, callee))), nil
	})}
	store := NewObservabilityStore("http://prometheus", "http://loki", "http://tempo", client, time.Now)
	server := NewServer(store, slog.New(slog.NewTextHandler(io.Discard, nil)), "test-token")
	request := httptest.NewRequest(http.MethodGet, "/v1/services/payment-service/observed-dependencies", nil)
	request.Header.Set("Authorization", "Bearer test-token")
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, request)
	var result ObservedTopology
	if err := json.Unmarshal(recorder.Body.Bytes(), &result); err != nil || recorder.Code != http.StatusOK || len(result.Calls) != 1 {
		t.Fatalf("observed endpoint failed: %v %s", err, recorder.Body.String())
	}
	if result.Source != "tempo_client_server_parentage" || result.Coverage != "sampled_not_exhaustive" || result.End.Sub(result.Start) != 30*time.Minute {
		t.Fatalf("misstated observation scope: %#v", result)
	}
}

func TestObservedCallsOutputIsBounded(t *testing.T) {
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Path == "/api/search" {
			return response(fmt.Sprintf(`{"traces":[{"traceID":%q,"startTimeUnixNano":%q}]}`, topologyTrace, strconv.FormatInt(topologyQuery().End.Add(-time.Minute).UnixNano(), 10))), nil
		}
		resources := []map[string]any{spanFixture("payment-service", "1111111111111111", "", 3)}
		for index := 1; index <= 70; index++ {
			resources = append(resources, spanFixture("ledger-service", fmt.Sprintf("%016x", index), "1111111111111111", 2))
		}
		return response(string(fixtureJSON(resources...))), nil
	})}
	store := NewObservabilityStore("http://prometheus", "http://loki", "http://tempo", client, time.Now)
	result, err := store.ObservedDependencies(context.Background(), topologyQuery())
	if err != nil || len(result.Calls) != 64 || result.CallsOmitted != 6 {
		t.Fatalf("call budget not enforced: %v %#v", err, result)
	}
}
