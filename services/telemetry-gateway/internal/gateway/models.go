package gateway

import (
	"context"
	"time"
)

type TelemetryStore interface {
	QueryMetrics(context.Context, MetricQuery) (MetricResult, error)
	SearchLogs(context.Context, LogSearch) ([]LogEvent, error)
	SearchTraces(context.Context, TraceSearch) ([]TraceSummary, error)
	Dependencies(string) []Dependency
	Deployments(string) []Deployment
}

type MetricQuery struct {
	Service  string    `json:"service"`
	Metric   string    `json:"metric"`
	Start    time.Time `json:"start"`
	End      time.Time `json:"end"`
	StepSecs int       `json:"step_seconds"`
}

type MetricPoint struct {
	Timestamp time.Time `json:"timestamp"`
	Value     float64   `json:"value"`
}

type MetricResult struct {
	Service    string        `json:"service"`
	Metric     string        `json:"metric"`
	Unit       string        `json:"unit"`
	Points     []MetricPoint `json:"points"`
	DataOrigin string        `json:"data_origin"`
}

type LogSearch struct {
	Service string    `json:"service"`
	Query   string    `json:"query"`
	Start   time.Time `json:"start"`
	End     time.Time `json:"end"`
	Limit   int       `json:"limit"`
}

type LogEvent struct {
	Timestamp time.Time         `json:"timestamp"`
	Level     string            `json:"level"`
	Message   string            `json:"message"`
	Fields    map[string]string `json:"fields"`
}

type TraceSearch struct {
	Service string    `json:"service"`
	Start   time.Time `json:"start"`
	End     time.Time `json:"end"`
	Limit   int       `json:"limit"`
}

type TraceSummary struct {
	TraceID         string    `json:"trace_id"`
	RootServiceName string    `json:"root_service_name"`
	RootTraceName   string    `json:"root_trace_name"`
	StartedAt       time.Time `json:"started_at"`
	DurationMs      float64   `json:"duration_ms"`
}

type Dependency struct {
	Source    string  `json:"source"`
	Target    string  `json:"target"`
	Protocol  string  `json:"protocol"`
	ErrorRate float64 `json:"error_rate"`
	P95Ms     float64 `json:"p95_ms"`
}

type Deployment struct {
	Service          string    `json:"service"`
	Revision         string    `json:"revision"`
	PreviousRevision string    `json:"previous_revision"`
	DeployedAt       time.Time `json:"deployed_at"`
	CommitSHA        string    `json:"commit_sha"`
	Summary          string    `json:"summary"`
}

type ErrorResponse struct {
	Error   string `json:"error"`
	TraceID string `json:"trace_id"`
}
