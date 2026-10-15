package main

import (
	"log/slog"
	"net/http"
	"os"
	"time"

	"github.com/example/ai-production-incident-copilot/telemetry-gateway/internal/gateway"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	token := os.Getenv("TELEMETRY_GATEWAY_TOKEN")
	if token == "" {
		token = "local-development-token"
		logger.Warn("using local development token")
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
		)
		logger.Info("using live observability backends",
			"prometheus_url", prometheusURL,
			"loki_url", lokiURL,
			"tempo_url", tempoURL,
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
