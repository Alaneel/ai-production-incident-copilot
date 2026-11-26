package gateway

import (
	"context"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func mustProfile(t *testing.T, raw string) *SignalProfile {
	t.Helper()
	profile, err := ParseSignalProfile([]byte(raw))
	if err != nil {
		t.Fatal(err)
	}
	return profile
}

const minimalProfile = `{
  "name": "team-stack",
  "metrics": {"request_latency_p95": {"promql": "p95_ms{app=\"{{service}}\"}", "unit": "milliseconds"}},
  "log_selector": "{app=\"{{service}}\"}",
  "trace_service_attribute": "resource.service.name",
  "dependencies": {"checkout": ["payments"]}
}`

func TestSignalProfileRejectsUnsafeOrAmbiguousConfiguration(t *testing.T) {
	cases := map[string]string{
		"unknown field":      `{"name":"x-y","metrics":{"request_latency_p95":{"promql":"a{s=\"{{service}}\"}"}},"extra":1}`,
		"unknown signal":     `{"name":"x-y","metrics":{"queue_depth":{"promql":"a{s=\"{{service}}\"}"}}}`,
		"alias name":         `{"name":"x-y","metrics":{"error_rate":{"promql":"a{s=\"{{service}}\"}"}}}`,
		"wrong unit":         `{"name":"x-y","metrics":{"request_latency_p95":{"promql":"a{s=\"{{service}}\"}","unit":"seconds"}}}`,
		"no placeholder":     `{"name":"x-y","metrics":{"request_latency_p95":{"promql":"sum(a)"}}}`,
		"no metrics":         `{"name":"x-y","metrics":{}}`,
		"bad name":           `{"name":"X","metrics":{"request_latency_p95":{"promql":"a{s=\"{{service}}\"}"}}}`,
		"log pipeline":       `{"name":"x-y","metrics":{"request_latency_p95":{"promql":"a{s=\"{{service}}\"}"}},"log_selector":"{app=\"{{service}}\"} |= \"x\""}`,
		"trace injection":    `{"name":"x-y","metrics":{"request_latency_p95":{"promql":"a{s=\"{{service}}\"}"}},"trace_service_attribute":"resource.service.name = \"a\" || true"}`,
		"self dependency":    `{"name":"x-y","metrics":{"request_latency_p95":{"promql":"a{s=\"{{service}}\"}"}},"dependencies":{"api":["api"]}}`,
		"invalid dependency": `{"name":"x-y","metrics":{"request_latency_p95":{"promql":"a{s=\"{{service}}\"}"}},"dependencies":{"api":["Bad Name"]}}`,
		"trailing content":   minimalProfile + `{}`,
	}
	for name, raw := range cases {
		if _, err := ParseSignalProfile([]byte(raw)); err == nil {
			t.Errorf("%s: expected rejection", name)
		}
	}
}

func TestCheckedInProfilesParseAndFaultLabMatchesBuiltInDefault(t *testing.T) {
	paths, err := filepath.Glob("../../../../deploy/signal-profiles/*.json")
	if err != nil || len(paths) < 3 {
		t.Fatalf("expected checked-in profiles, got %v (%v)", paths, err)
	}
	for _, path := range paths {
		profile, err := LoadSignalProfile(path)
		if err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		if filepath.Base(path) != profile.Name+".json" {
			t.Errorf("%s: name %q should match the file name", path, profile.Name)
		}
	}
	file, err := LoadSignalProfile("../../../../deploy/signal-profiles/fault-lab.json")
	if err != nil {
		t.Fatal(err)
	}
	builtin := DefaultSignalProfile()
	for _, signal := range KnownSignals() {
		want, _, _ := builtin.metric(signal)
		got, _, ok := file.metric(signal)
		if !ok || got.query("payment-service") != want.query("payment-service") || got.unit != want.unit {
			t.Errorf("%s: fault-lab.json drifted from the built-in default", signal)
		}
	}
	if file.logQuery("payment-service") != builtin.logQuery("payment-service") ||
		file.traceQuery("payment-service") != builtin.traceQuery("payment-service") {
		t.Error("fault-lab.json log/trace selectors drifted from the built-in default")
	}
}

func TestProfileDrivesLiveQueriesAndReportsUnconfiguredSignalsAsAbsent(t *testing.T) {
	var seen []string
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		seen = append(seen, request.URL.Path+" "+request.URL.Query().Get("query")+request.URL.Query().Get("q"))
		switch request.URL.Path {
		case "/api/v1/query_range":
			return response(`{"status":"success","data":{"result":[{"values":[[1784786400,"120"]]}]}}`), nil
		case "/loki/api/v1/query_range":
			return response(`{"status":"success","data":{"result":[]}}`), nil
		default:
			return response(`{"traces":[]}`), nil
		}
	})}
	store := NewObservabilityStore("http://p", "http://l", "http://t", client, time.Now).
		WithSignalProfile(mustProfile(t, minimalProfile))
	start, end := time.Now().Add(-time.Hour), time.Now()

	result, err := store.QueryMetrics(context.Background(), MetricQuery{Service: "checkout", Metric: "request_latency_p95", Start: start, End: end, StepSecs: 60})
	if err != nil || len(result.Points) != 1 || result.DataOrigin != "prometheus_api" {
		t.Fatalf("configured signal: %#v %v", result, err)
	}
	if !strings.Contains(seen[0], `p95_ms{app="checkout"}`) {
		t.Fatalf("template not rendered: %s", seen[0])
	}
	absent, err := store.QueryMetrics(context.Background(), MetricQuery{Service: "checkout", Metric: "request_error_rate", Start: start, End: end, StepSecs: 60})
	if err != nil || len(absent.Points) != 0 || absent.DataOrigin != "not_configured" || absent.Unit != "ratio" {
		t.Fatalf("unconfigured signal must be absent, not zero: %#v %v", absent, err)
	}
	if len(seen) != 1 {
		t.Fatalf("unconfigured signal must not query Prometheus: %v", seen)
	}
	if _, err := store.QueryMetrics(context.Background(), MetricQuery{Service: "checkout", Metric: "rm -rf", Start: start, End: end, StepSecs: 60}); err == nil {
		t.Fatal("unknown metric must stay rejected")
	}
	if _, err := store.SearchLogs(context.Background(), LogSearch{Service: "checkout", Query: "timeout", Start: start, End: end, Limit: 5}); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(seen[1], `{app="checkout"} |~`) {
		t.Fatalf("log selector not applied: %s", seen[1])
	}
	edges := store.Dependencies("payments")
	if len(edges) != 1 || edges[0].Source != "checkout" || edges[0].Protocol != "declared" || !store.DeclaresDependencies() {
		t.Fatalf("declared dependencies: %#v", edges)
	}
}

func TestCheckReportsOkEmptyErrorAndSkippedSignals(t *testing.T) {
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		query := request.URL.Query().Get("query")
		switch {
		case strings.Contains(query, "p95_ms"):
			return response(`{"status":"success","data":{"result":[{"values":[[1784786400,"120"],[1784786460,"180"]]}]}}`), nil
		case strings.Contains(query, "errors_total"):
			return response(`{"status":"success","data":{"result":[]}}`), nil
		case request.URL.Path == "/loki/api/v1/query_range":
			return &http.Response{StatusCode: http.StatusBadGateway, Body: http.NoBody}, nil
		default:
			return response(`{"traces":[{"traceID":"0af7651916cd43dd8448eb211c80319c","startTimeUnixNano":"1784786400000000000"}]}`), nil
		}
	})}
	profile := mustProfile(t, `{"name":"team-stack","metrics":{
	  "request_latency_p95":{"promql":"p95_ms{app=\"{{service}}\"}"},
	  "request_error_rate":{"promql":"errors_total{app=\"{{service}}\"}"}}}`)
	store := NewObservabilityStore("http://p", "http://l", "http://t", client, time.Now).WithSignalProfile(profile)
	report, err := RunCheck(context.Background(), store, "checkout", time.Hour, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	status := map[string]CheckStatus{}
	for _, result := range report.Results {
		status[result.Signal] = result.Status
	}
	want := map[string]CheckStatus{"request_latency_p95": CheckOK, "request_error_rate": CheckEmpty,
		"cpu_utilization": CheckNotConfigured, "logs": CheckError, "traces": CheckOK, "dependencies": CheckEmpty}
	for signal, expected := range want {
		if status[signal] != expected {
			t.Errorf("%s: got %s, want %s", signal, status[signal], expected)
		}
	}
	if !report.Failed() {
		t.Error("a backend error must fail the check")
	}
	var text strings.Builder
	report.WriteText(&text, false)
	for _, fragment := range []string{"profile team-stack", "↳ errors_total{app=\"checkout\"}", "1 errors"} {
		if !strings.Contains(text.String(), fragment) {
			t.Errorf("report text missing %q:\n%s", fragment, text.String())
		}
	}
	if _, err := RunCheck(context.Background(), store, "Bad Service", time.Hour, time.Now()); err == nil {
		t.Error("invalid service must be rejected before any query")
	}
}

func TestLoadSignalProfileReportsMissingFile(t *testing.T) {
	if _, err := LoadSignalProfile(filepath.Join(t.TempDir(), "absent.json")); err == nil || !os.IsNotExist(unwrapAll(err)) {
		t.Fatalf("expected not-exist error, got %v", err)
	}
}

func unwrapAll(err error) error {
	for {
		next, ok := err.(interface{ Unwrap() error })
		if !ok || next.Unwrap() == nil {
			return err
		}
		err = next.Unwrap()
	}
}

func TestFixtureRatiosNeverExceedOne(t *testing.T) {
	store := NewFixtureStore(time.Now)
	start := time.Now().Add(-30 * time.Minute)
	for _, metric := range []string{"db_pool_active_ratio", "request_error_rate", "cpu_utilization"} {
		result, err := store.QueryMetrics(context.Background(), MetricQuery{Service: "payment-service", Metric: metric, Start: start, End: start.Add(16 * time.Minute)})
		if err != nil {
			t.Fatal(err)
		}
		peak := 0.0
		for _, point := range result.Points {
			if point.Value > 1 {
				t.Fatalf("%s fixture sample %.3f exceeds a ratio of 1", metric, point.Value)
			}
			peak = max(peak, point.Value)
		}
		if metric == "db_pool_active_ratio" && peak != 1 {
			t.Fatalf("payment-service pool fixture should saturate at 1.0, peaked at %.3f", peak)
		}
	}
}
