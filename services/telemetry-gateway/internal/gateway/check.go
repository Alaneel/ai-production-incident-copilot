package gateway

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/url"
	"strings"
	"text/tabwriter"
	"time"
)

// CheckStatus is the outcome of probing one signal against live backends.
type CheckStatus string

const (
	CheckOK            CheckStatus = "ok"
	CheckEmpty         CheckStatus = "empty"
	CheckError         CheckStatus = "error"
	CheckNotConfigured CheckStatus = "not_configured"
)

type CheckResult struct {
	Signal  string      `json:"signal"`
	Backend string      `json:"backend"`
	Status  CheckStatus `json:"status"`
	Detail  string      `json:"detail"`
	Query   string      `json:"query,omitempty"`
}

type CheckReport struct {
	Profile string        `json:"profile"`
	Service string        `json:"service"`
	Window  string        `json:"window"`
	Results []CheckResult `json:"results"`
}

// Failed reports backend or query errors. Empty and unconfigured signals are
// warnings: a quiet service legitimately returns no error logs or traces.
func (r CheckReport) Failed() bool {
	for _, result := range r.Results {
		if result.Status == CheckError {
			return true
		}
	}
	return false
}

// RunCheck issues the same read-only queries diagnosis would, for one service,
// so an operator can see which signals their environment actually answers.
func RunCheck(ctx context.Context, store *ObservabilityStore, service string, window time.Duration, now time.Time) (CheckReport, error) {
	if !validService(service) {
		return CheckReport{}, fmt.Errorf("invalid service name %q: use 2-80 lowercase letters, digits or hyphens", service)
	}
	if window <= 0 || window > 24*time.Hour {
		return CheckReport{}, fmt.Errorf("window must be between 1m and 24h")
	}
	end := now.UTC()
	start := end.Add(-window)
	step := int(window.Seconds()) / 120
	if step < 15 {
		step = 15
	}
	report := CheckReport{Profile: store.profile.Name, Service: service, Window: window.String()}
	for _, signal := range checkOrder() {
		definition, _, configured := store.profile.metric(signal)
		if !configured {
			report.Results = append(report.Results, CheckResult{Signal: signal, Backend: "prometheus", Status: CheckNotConfigured,
				Detail: "not in this profile; diagnosis records it as unavailable"})
			continue
		}
		query := definition.query(service)
		result, err := store.QueryMetrics(ctx, MetricQuery{Service: service, Metric: signal, Start: start, End: end, StepSecs: step})
		switch {
		case err != nil:
			report.Results = append(report.Results, CheckResult{Signal: signal, Backend: "prometheus", Status: CheckError,
				Detail: describe(err), Query: query})
		case len(result.Points) == 0:
			report.Results = append(report.Results, CheckResult{Signal: signal, Backend: "prometheus", Status: CheckEmpty,
				Detail: "no samples: check the metric name and the label that selects " + service, Query: query})
		default:
			last := result.Points[len(result.Points)-1].Value
			report.Results = append(report.Results, CheckResult{Signal: signal, Backend: "prometheus", Status: CheckOK,
				Detail: fmt.Sprintf("%d samples, latest %.4g %s", len(result.Points), last, result.Unit), Query: query})
		}
	}

	logQuery := store.profile.logQuery(service)
	events, err := store.SearchLogs(ctx, LogSearch{Service: service, Start: start, End: end, Limit: 20})
	report.Results = append(report.Results, countResult("logs", "loki", logQuery, len(events), err,
		"no log lines: check that log_selector matches how "+service+" is labelled"))

	traceQuery := store.profile.traceQuery(service)
	traces, err := store.SearchTraces(ctx, TraceSearch{Service: service, Start: start, End: end, Limit: 5})
	report.Results = append(report.Results, countResult("traces", "tempo", traceQuery, len(traces), err,
		"no traces: check trace_service_attribute and that "+service+" exports spans"))

	edges := store.Dependencies(service)
	source := "built-in demo catalog (declare dependencies in the profile)"
	if store.DeclaresDependencies() {
		source = "profile dependencies"
	}
	status := CheckOK
	if len(edges) == 0 {
		status = CheckEmpty
	}
	report.Results = append(report.Results, CheckResult{Signal: "dependencies", Backend: "catalog", Status: status,
		Detail: fmt.Sprintf("%d edges from %s", len(edges), source)})
	return report, nil
}

func countResult(signal, backend, query string, count int, err error, emptyHint string) CheckResult {
	switch {
	case err != nil:
		return CheckResult{Signal: signal, Backend: backend, Status: CheckError, Detail: describe(err), Query: query}
	case count == 0:
		return CheckResult{Signal: signal, Backend: backend, Status: CheckEmpty, Detail: emptyHint, Query: query}
	default:
		return CheckResult{Signal: signal, Backend: backend, Status: CheckOK, Detail: fmt.Sprintf("%d results", count), Query: query}
	}
}

// checkOrder lists the primary SLI signals first, then saturation signals.
func checkOrder() []string {
	order := []string{"request_latency_p95", "request_error_rate", "db_pool_active_ratio", "db_pool_active",
		"cpu_utilization", "memory_working_set", "kafka_consumer_lag", "retry_rate", "cache_miss_rate"}
	seen := map[string]bool{}
	for _, name := range order {
		seen[name] = true
	}
	for _, name := range KnownSignals() {
		if !seen[name] {
			order = append(order, name)
		}
	}
	return order
}

// describe reduces transport errors to the host and cause; the request URL
// would otherwise bury the actionable part (refused, timeout, TLS, 401...).
func describe(err error) string {
	var transport *url.Error
	if errors.As(err, &transport) {
		host := transport.URL
		if parsed, parseErr := url.Parse(transport.URL); parseErr == nil {
			host = parsed.Host
		}
		cause := transport.Err.Error()
		if transport.Timeout() {
			cause = "timed out"
		}
		return oneLine("cannot reach " + host + ": " + cause)
	}
	return oneLine(err.Error())
}

func oneLine(value string) string {
	value = strings.Join(strings.Fields(value), " ")
	if len(value) > 220 {
		value = value[:217] + "..."
	}
	return value
}

// WriteText renders a report for a terminal.
func (r CheckReport) WriteText(output io.Writer, verbose bool) {
	fmt.Fprintf(output, "OpsPilot telemetry check · profile %s · service %s · last %s\n\n", r.Profile, r.Service, r.Window)
	table := tabwriter.NewWriter(output, 0, 0, 2, ' ', 0)
	marks := map[CheckStatus]string{CheckOK: "ok", CheckEmpty: "empty", CheckError: "ERROR", CheckNotConfigured: "skip"}
	fmt.Fprintln(table, "SIGNAL\tSOURCE\tSTATUS\tDETAIL")
	var skipped []string
	for _, result := range r.Results {
		if result.Status == CheckNotConfigured {
			skipped = append(skipped, result.Signal)
			continue
		}
		fmt.Fprintf(table, "%s\t%s\t%s\t%s\n", result.Signal, result.Backend, marks[result.Status], result.Detail)
		if verbose || (result.Status != CheckOK && result.Query != "") {
			if result.Query != "" {
				fmt.Fprintf(table, "\t\t\t↳ %s\n", oneLine(result.Query))
			}
		}
	}
	table.Flush()
	if len(skipped) > 0 {
		fmt.Fprintf(output, "\nNot in this profile (diagnosis records them as unavailable): %s\n", strings.Join(skipped, ", "))
	}
	counts := map[CheckStatus]int{}
	for _, result := range r.Results {
		counts[result.Status]++
	}
	fmt.Fprintf(output, "\n%d ok · %d empty · %d errors · %d not configured\n",
		counts[CheckOK], counts[CheckEmpty], counts[CheckError], counts[CheckNotConfigured])
	if counts[CheckOK] == 0 {
		fmt.Fprintln(output, "No signal returned data. Diagnosis would have no evidence for this service.")
	}
}
