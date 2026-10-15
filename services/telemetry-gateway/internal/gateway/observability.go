package gateway

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

type ObservabilityStore struct {
	prometheusURL string
	lokiURL       string
	tempoURL      string
	client        *http.Client
	catalog       *FixtureStore
}

type metricDefinition struct {
	query func(string) string
	unit  string
}

var allowedMetrics = map[string]metricDefinition{
	"request_latency_p95": {
		query: func(service string) string {
			return fmt.Sprintf(
				`histogram_quantile(0.95, sum by (le) (rate(incident_http_request_duration_seconds_bucket{service=%q}[5m]))) * 1000`,
				service,
			)
		},
		unit: "milliseconds",
	},
	"db_pool_active": {
		query: func(service string) string {
			return fmt.Sprintf(`incident_db_pool_active{service=%q}`, service)
		},
		unit: "connections",
	},
	"db_pool_utilization": {
		query: func(service string) string {
			return fmt.Sprintf(
				`incident_db_pool_active{service=%q} / incident_db_pool_max{service=%q}`,
				service,
				service,
			)
		},
		unit: "ratio",
	},
	"error_rate": {
		query: func(service string) string {
			return fmt.Sprintf(
				`sum(rate(incident_http_requests_total{service=%q,status="error"}[5m])) / clamp_min(sum(rate(incident_http_requests_total{service=%q}[5m])), 0.000001)`,
				service,
				service,
			)
		},
		unit: "ratio",
	},
	"cpu_load": {
		query: func(service string) string {
			return fmt.Sprintf(`incident_fault_cpu_load{service=%q}`, service)
		},
		unit: "ratio",
	},
	"kafka_consumer_lag": {
		query: func(service string) string {
			return fmt.Sprintf(`incident_kafka_consumer_lag{service=%q}`, service)
		},
		unit: "messages",
	},
	"memory_working_set": {
		query: func(service string) string {
			return fmt.Sprintf(`incident_fault_memory_bytes{service=%q}`, service)
		},
		unit: "bytes",
	},
	"retry_rate": {
		query: func(service string) string {
			return fmt.Sprintf(`incident_retry_rate{service=%q}`, service)
		},
		unit: "retries_per_second",
	},
	"cache_miss_rate": {
		query: func(service string) string {
			return fmt.Sprintf(`incident_cache_miss_rate{service=%q}`, service)
		},
		unit: "misses_per_second",
	},
}

func init() {
	allowedMetrics["request_error_rate"] = allowedMetrics["error_rate"]
	allowedMetrics["db_pool_active_ratio"] = allowedMetrics["db_pool_utilization"]
	allowedMetrics["cpu_utilization"] = allowedMetrics["cpu_load"]
}

func NewObservabilityStore(
	prometheusURL string,
	lokiURL string,
	tempoURL string,
	client *http.Client,
	now func() time.Time,
) *ObservabilityStore {
	return &ObservabilityStore{
		prometheusURL: strings.TrimRight(prometheusURL, "/"),
		lokiURL:       strings.TrimRight(lokiURL, "/"),
		tempoURL:      strings.TrimRight(tempoURL, "/"),
		client:        client,
		catalog:       NewFixtureStore(now),
	}
}

func (s *ObservabilityStore) SearchTraces(
	ctx context.Context,
	query TraceSearch,
) ([]TraceSummary, error) {
	// Tempo otherwise stops at the first matching N traces, not the newest N.
	// Sorting a truncated response locally cannot recover omitted current faults.
	traceQL := fmt.Sprintf(`{ resource.service.name = %q } with (most_recent=true)`, query.Service)
	parameters := url.Values{
		"q":     {traceQL},
		"start": {strconv.FormatInt(query.Start.Unix(), 10)},
		"end":   {strconv.FormatInt(query.End.Unix(), 10)},
		"limit": {strconv.Itoa(query.Limit)},
	}
	endpoint := s.tempoURL + "/api/search?" + parameters.Encode()
	var response struct {
		Traces []struct {
			TraceID           string  `json:"traceID"`
			RootServiceName   string  `json:"rootServiceName"`
			RootTraceName     string  `json:"rootTraceName"`
			StartTimeUnixNano string  `json:"startTimeUnixNano"`
			DurationMs        float64 `json:"durationMs"`
		} `json:"traces"`
	}
	if err := s.getJSON(ctx, endpoint, &response); err != nil {
		return nil, fmt.Errorf("tempo search failed: %w", err)
	}
	traces := make([]TraceSummary, 0, len(response.Traces))
	for _, item := range response.Traces {
		traceID := canonicalTempoSearchID(item.TraceID)
		if traceID == "" {
			continue
		}
		nanoseconds, err := strconv.ParseInt(item.StartTimeUnixNano, 10, 64)
		if err != nil {
			continue
		}
		traces = append(traces, TraceSummary{
			TraceID:         traceID,
			RootServiceName: item.RootServiceName,
			RootTraceName:   item.RootTraceName,
			StartedAt:       time.Unix(0, nanoseconds).UTC(),
			DurationMs:      item.DurationMs,
		})
	}
	return traces, nil
}

func (s *ObservabilityStore) QueryMetrics(
	ctx context.Context,
	query MetricQuery,
) (MetricResult, error) {
	definition, ok := allowedMetrics[query.Metric]
	if !ok {
		return MetricResult{}, fmt.Errorf("metric %q is not allow-listed", query.Metric)
	}
	parameters := url.Values{
		"query": {definition.query(query.Service)},
		"start": {query.Start.Format(time.RFC3339Nano)},
		"end":   {query.End.Format(time.RFC3339Nano)},
		"step":  {strconv.Itoa(query.StepSecs)},
	}
	endpoint := s.prometheusURL + "/api/v1/query_range?" + parameters.Encode()
	var response struct {
		Status string `json:"status"`
		Error  string `json:"error"`
		Data   struct {
			Result []struct {
				Values [][]json.RawMessage `json:"values"`
			} `json:"result"`
		} `json:"data"`
	}
	if err := s.getJSON(ctx, endpoint, &response); err != nil {
		return MetricResult{}, fmt.Errorf("prometheus query failed: %w", err)
	}
	if response.Status != "success" {
		return MetricResult{}, fmt.Errorf("prometheus rejected query: %s", response.Error)
	}

	points := make([]MetricPoint, 0)
	for _, series := range response.Data.Result {
		for _, value := range series.Values {
			if len(value) != 2 {
				continue
			}
			var timestamp float64
			var rawValue string
			if err := json.Unmarshal(value[0], &timestamp); err != nil {
				continue
			}
			if err := json.Unmarshal(value[1], &rawValue); err != nil {
				continue
			}
			number, err := strconv.ParseFloat(rawValue, 64)
			if err != nil || math.IsNaN(number) || math.IsInf(number, 0) {
				continue
			}
			points = append(points, MetricPoint{
				Timestamp: time.Unix(0, int64(timestamp*float64(time.Second))).UTC(),
				Value:     number,
			})
		}
	}
	sort.Slice(points, func(left, right int) bool {
		return points[left].Timestamp.Before(points[right].Timestamp)
	})
	return MetricResult{
		Service:    query.Service,
		Metric:     query.Metric,
		Unit:       definition.unit,
		Points:     points,
		DataOrigin: "prometheus_api",
	}, nil
}

func (s *ObservabilityStore) SearchLogs(
	ctx context.Context,
	query LogSearch,
) ([]LogEvent, error) {
	logPattern := regexp.QuoteMeta(strings.TrimSpace(query.Query))
	logQL := fmt.Sprintf(`{service=%q}`, query.Service)
	if logPattern != "" {
		logQL += fmt.Sprintf(` |~ %q`, "(?i)"+logPattern)
	}
	parameters := url.Values{
		"query":     {logQL},
		"start":     {strconv.FormatInt(query.Start.UnixNano(), 10)},
		"end":       {strconv.FormatInt(query.End.UnixNano(), 10)},
		"limit":     {strconv.Itoa(query.Limit)},
		"direction": {"backward"},
	}
	endpoint := s.lokiURL + "/loki/api/v1/query_range?" + parameters.Encode()
	var response struct {
		Status string `json:"status"`
		Data   struct {
			Result []struct {
				Stream map[string]string `json:"stream"`
				Values [][]string        `json:"values"`
			} `json:"result"`
		} `json:"data"`
	}
	if err := s.getJSON(ctx, endpoint, &response); err != nil {
		return nil, fmt.Errorf("loki query failed: %w", err)
	}
	if response.Status != "success" {
		return nil, errors.New("loki rejected query")
	}

	events := make([]LogEvent, 0, query.Limit)
	for _, stream := range response.Data.Result {
		for _, value := range stream.Values {
			if len(value) != 2 || len(events) >= query.Limit {
				continue
			}
			nanoseconds, err := strconv.ParseInt(value[0], 10, 64)
			if err != nil {
				continue
			}
			fields := map[string]string{}
			message := value[1]
			level := stream.Stream["level"]
			var record map[string]any
			if json.Unmarshal([]byte(value[1]), &record) == nil {
				if parsed, ok := record["message"].(string); ok {
					message = parsed
				}
				if parsed, ok := record["level"].(string); ok {
					level = parsed
				}
				for _, key := range []string{
					"service", "pool", "active", "max", "trace_id", "span_id", "request_id", "fault",
				} {
					if parsed, ok := record[key]; ok {
						fields[key] = fmt.Sprint(parsed)
					}
				}
			}
			if fields["service"] == "" {
				fields["service"] = stream.Stream["service"]
			}
			if level == "" {
				level = "INFO"
			}
			events = append(events, LogEvent{
				Timestamp: time.Unix(0, nanoseconds).UTC(),
				Level:     strings.ToUpper(level),
				Message:   message,
				Fields:    fields,
			})
		}
	}
	sort.Slice(events, func(left, right int) bool {
		return events[left].Timestamp.Before(events[right].Timestamp)
	})
	return events, nil
}

func (s *ObservabilityStore) Dependencies(service string) []Dependency {
	return s.catalog.Dependencies(service)
}

func (s *ObservabilityStore) Deployments(service string) []Deployment {
	return s.catalog.Deployments(service)
}

func (s *ObservabilityStore) getJSON(
	ctx context.Context,
	endpoint string,
	destination any,
) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return err
	}
	response, err := s.client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		body, _ := io.ReadAll(io.LimitReader(response.Body, 4096))
		return fmt.Errorf("upstream status %d: %s", response.StatusCode, strings.TrimSpace(string(body)))
	}
	decoder := json.NewDecoder(io.LimitReader(response.Body, 4<<20))
	return decoder.Decode(destination)
}
