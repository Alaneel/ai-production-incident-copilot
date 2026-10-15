package gateway

import (
	"context"
	"fmt"
	"strings"
	"time"
)

type FixtureStore struct {
	now func() time.Time
}

func NewFixtureStore(now func() time.Time) *FixtureStore {
	return &FixtureStore{now: now}
}

func (s *FixtureStore) QueryMetrics(_ context.Context, query MetricQuery) (MetricResult, error) {
	points := make([]MetricPoint, 0, 16)
	baseline, anomalous, unit := fixtureMetricShape(query.Metric, query.Service)
	for index := 0; index < 16; index++ {
		value := baseline
		if index >= 9 {
			value = anomalous + float64(index-9)*(anomalous*0.03)
		}
		points = append(points, MetricPoint{
			Timestamp: query.Start.Add(time.Duration(index) * time.Minute),
			Value:     value,
		})
	}
	return MetricResult{
		Service:    query.Service,
		Metric:     query.Metric,
		Unit:       unit,
		Points:     points,
		DataOrigin: "synthetic",
	}, nil
}

func fixtureMetricShape(metric string, service string) (float64, float64, string) {
	switch metric {
	case "db_pool_active_ratio", "db_pool_utilization":
		if service == "payment-service" {
			return 0.46, 1.0, "ratio"
		}
		return 0.46, 0.46, "ratio"
	case "request_error_rate", "error_rate":
		return 0.01, 0.01, "ratio"
	case "cpu_utilization", "cpu_load":
		return 0.38, 0.38, "ratio"
	case "memory_working_set":
		return 64 * 1024 * 1024, 64 * 1024 * 1024, "bytes"
	case "kafka_consumer_lag":
		return 0, 0, "messages"
	case "retry_rate":
		return 0, 0, "retries_per_second"
	case "cache_miss_rate":
		return 0, 0, "misses_per_second"
	default:
		return 630, 2360, "milliseconds"
	}
}

func (s *FixtureStore) SearchLogs(_ context.Context, query LogSearch) ([]LogEvent, error) {
	events := make([]LogEvent, 0, query.Limit)
	limit := min(query.Limit, 25)
	message := query.Query
	if message == "" {
		message = "request completed"
	}
	for index := 0; index < limit; index++ {
		fields := map[string]string{
			"service":  query.Service,
			"trace_id": fmt.Sprintf("trace-%03d", index),
		}
		if strings.Contains(message, "acquisition timed out") {
			fields["pool"] = "ledger-primary"
			fields["active"] = "50"
			fields["max"] = "50"
		}
		events = append(events, LogEvent{
			Timestamp: query.Start.Add(time.Duration(index) * 3 * time.Second),
			Level:     "ERROR",
			Message:   message,
			Fields:    fields,
		})
	}
	return events, nil
}

func (s *FixtureStore) SearchTraces(
	_ context.Context,
	query TraceSearch,
) ([]TraceSummary, error) {
	limit := min(query.Limit, 3)
	traces := make([]TraceSummary, 0, limit)
	for index := 0; index < limit; index++ {
		traces = append(traces, TraceSummary{
			TraceID:         fmt.Sprintf("trace-%s-%03d", query.Service, index),
			RootServiceName: "checkout-api",
			RootTraceName:   "POST /v1/checkout",
			StartedAt:       query.Start.Add(time.Duration(index) * time.Minute),
			DurationMs:      2840 + float64(index*120),
		})
	}
	return traces, nil
}

func (s *FixtureStore) Dependencies(service string) []Dependency {
	switch service {
	case "payment-service":
		return []Dependency{
			{Source: "checkout-api", Target: "payment-service", Protocol: "grpc", ErrorRate: 0.127, P95Ms: 2840},
			{Source: "payment-service", Target: "ledger-service", Protocol: "grpc", ErrorRate: 0.184, P95Ms: 1760},
			{Source: "payment-service", Target: "risk-engine", Protocol: "grpc", ErrorRate: 0.002, P95Ms: 112},
		}
	case "checkout-api":
		return []Dependency{
			{Source: "checkout-api", Target: "payment-service", Protocol: "grpc", ErrorRate: 0.127, P95Ms: 2840},
		}
	case "ledger-service":
		return []Dependency{
			{Source: "payment-service", Target: "ledger-service", Protocol: "grpc", ErrorRate: 0.184, P95Ms: 1760},
		}
	case "risk-engine":
		return []Dependency{
			{Source: "payment-service", Target: "risk-engine", Protocol: "grpc", ErrorRate: 0.002, P95Ms: 112},
		}
	default:
		return []Dependency{}
	}
}

func (s *FixtureStore) Deployments(service string) []Deployment {
	return []Deployment{
		{
			Service:          service,
			Revision:         "142",
			PreviousRevision: "141",
			DeployedAt:       s.now().Add(-29 * time.Minute),
			CommitSHA:        "7b4d23e",
			Summary:          "Parallelize ledger balance lookups",
		},
	}
}
