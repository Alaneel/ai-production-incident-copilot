package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"time"

	"github.com/example/ai-production-incident-copilot/telemetry-gateway/internal/gateway"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "check" {
		os.Exit(runCheck(os.Args[2:]))
	}
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	token := os.Getenv("TELEMETRY_GATEWAY_TOKEN")
	if token == "" {
		token = "local-development-token"
		logger.Warn("using local development token")
	}

	profile, err := signalProfile()
	if err != nil {
		logger.Error("invalid signal profile", "error", err)
		os.Exit(2)
	}
	prometheusURL := os.Getenv("PROMETHEUS_URL")
	lokiURL := os.Getenv("LOKI_URL")
	tempoURL := os.Getenv("TEMPO_URL")
	var store gateway.TelemetryStore
	if prometheusURL != "" && lokiURL != "" && tempoURL != "" {
		store = gateway.NewObservabilityStore(
			prometheusURL,
			lokiURL,
			tempoURL,
			&http.Client{Timeout: 8 * time.Second},
			time.Now,
		).WithSignalProfile(profile)
		logger.Info("using live observability backends",
			"prometheus_url", prometheusURL,
			"loki_url", lokiURL,
			"tempo_url", tempoURL,
			"signal_profile", profile.Name,
			"signals", profile.ConfiguredSignals(),
			"declared_dependencies", profile.DeclaresDependencies(),
		)
	} else {
		store = gateway.NewFixtureStore(time.Now)
		logger.Warn("using deterministic telemetry fixtures")
	}
	server := gateway.NewServer(store, logger, token)
	address := os.Getenv("TELEMETRY_GATEWAY_ADDRESS")
	if address == "" {
		address = ":8081"
	}
	httpServer := &http.Server{
		Addr:              address,
		Handler:           server.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      15 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	logger.Info("telemetry gateway listening", "address", httpServer.Addr)
	if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		logger.Error("telemetry gateway stopped", "error", err)
		os.Exit(1)
	}
}

// signalProfile loads TELEMETRY_SIGNAL_PROFILE, or the fault-lab default.
func signalProfile() (*gateway.SignalProfile, error) {
	path := os.Getenv("TELEMETRY_SIGNAL_PROFILE")
	if path == "" {
		return gateway.DefaultSignalProfile(), nil
	}
	return gateway.LoadSignalProfile(path)
}

// runCheck probes live backends with the same read-only queries diagnosis uses.
func runCheck(arguments []string) int {
	flags := flag.NewFlagSet("check", flag.ContinueOnError)
	service := flags.String("service", "", "service name to check (required)")
	window := flags.Duration("window", time.Hour, "how far back to look")
	asJSON := flags.Bool("json", false, "print a JSON report")
	verbose := flags.Bool("v", false, "print every rendered query")
	flags.Usage = func() {
		fmt.Fprintln(flags.Output(), "Usage: gateway check --service NAME [--window 1h] [--json] [-v]")
		fmt.Fprintln(flags.Output(), "Reads PROMETHEUS_URL, LOKI_URL, TEMPO_URL and optional TELEMETRY_SIGNAL_PROFILE.")
		flags.PrintDefaults()
	}
	if err := flags.Parse(arguments); err != nil {
		return 2
	}
	if *service == "" {
		flags.Usage()
		return 2
	}
	urls := map[string]string{"PROMETHEUS_URL": os.Getenv("PROMETHEUS_URL"), "LOKI_URL": os.Getenv("LOKI_URL"), "TEMPO_URL": os.Getenv("TEMPO_URL")}
	for name, value := range urls {
		if value == "" {
			fmt.Fprintf(os.Stderr, "%s is not set; the check needs live backends.\n", name)
			return 2
		}
	}
	profile, err := signalProfile()
	if err != nil {
		fmt.Fprintln(os.Stderr, "invalid signal profile:", err)
		return 2
	}
	store := gateway.NewObservabilityStore(urls["PROMETHEUS_URL"], urls["LOKI_URL"], urls["TEMPO_URL"],
		&http.Client{Timeout: 8 * time.Second}, time.Now).WithSignalProfile(profile)
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	report, err := gateway.RunCheck(ctx, store, *service, *window, time.Now())
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 2
	}
	if *asJSON {
		encoder := json.NewEncoder(os.Stdout)
		encoder.SetIndent("", "  ")
		_ = encoder.Encode(report)
	} else {
		report.WriteText(os.Stdout, *verbose)
	}
	if report.Failed() {
		return 1
	}
	return 0
}
