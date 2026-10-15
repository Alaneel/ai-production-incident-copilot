package gateway

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"strconv"
	"strings"
	"time"
)

type contextKey string

const traceIDKey contextKey = "trace-id"
const defaultRequestsPerMinute = 120

type Server struct {
	store       TelemetryStore
	logger      *slog.Logger
	token       string
	rateLimiter *RateLimiter
	mux         *http.ServeMux
}

func NewServer(store TelemetryStore, logger *slog.Logger, token string) *Server {
	server := &Server{
		store:       store,
		logger:      logger,
		token:       token,
		rateLimiter: NewRateLimiter(defaultRequestsPerMinute, time.Minute),
		mux:         http.NewServeMux(),
	}
	server.routes()
	return server
}

func (s *Server) Handler() http.Handler {
	return s.trace(s.rateLimit(s.authenticate(s.audit(s.mux))))
}

func (s *Server) routes() {
	s.mux.HandleFunc("GET /health", s.health)
	s.mux.HandleFunc("POST /v1/metrics/query", s.queryMetrics)
	s.mux.HandleFunc("POST /v1/logs/search", s.searchLogs)
	s.mux.HandleFunc("POST /v1/traces/search", s.searchTraces)
	s.mux.HandleFunc("GET /v1/services/{service}/dependencies", s.dependencies)
	s.mux.HandleFunc("GET /v1/services/{service}/observed-dependencies", s.observedDependencies)
	s.mux.HandleFunc("GET /v1/services/{service}/deployments", s.deployments)
}

func (s *Server) searchTraces(writer http.ResponseWriter, request *http.Request) {
	var query TraceSearch
	if err := decodeJSON(request, &query); err != nil {
		s.writeError(writer, request, http.StatusBadRequest, err)
		return
	}
	if query.Limit == 0 {
		query.Limit = 20
	}
	if err := validateTraceSearch(query); err != nil {
		s.writeError(writer, request, http.StatusUnprocessableEntity, err)
		return
	}
	traces, err := s.store.SearchTraces(request.Context(), query)
	if err != nil {
		s.writeError(writer, request, http.StatusBadGateway, err)
		return
	}
	writeJSON(writer, http.StatusOK, map[string]any{
		"traces": traces,
		"count":  len(traces),
	})
}

func (s *Server) health(writer http.ResponseWriter, _ *http.Request) {
	writeJSON(writer, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) queryMetrics(writer http.ResponseWriter, request *http.Request) {
	var query MetricQuery
	if err := decodeJSON(request, &query); err != nil {
		s.writeError(writer, request, http.StatusBadRequest, err)
		return
	}
	if err := validateMetricQuery(query); err != nil {
		s.writeError(writer, request, http.StatusUnprocessableEntity, err)
		return
	}
	result, err := s.store.QueryMetrics(request.Context(), query)
	if err != nil {
		s.writeError(writer, request, http.StatusBadGateway, err)
		return
	}
	writeJSON(writer, http.StatusOK, result)
}

func (s *Server) searchLogs(writer http.ResponseWriter, request *http.Request) {
	var query LogSearch
	if err := decodeJSON(request, &query); err != nil {
		s.writeError(writer, request, http.StatusBadRequest, err)
		return
	}
	if query.Limit == 0 {
		query.Limit = 100
	}
	if err := validateLogSearch(query); err != nil {
		s.writeError(writer, request, http.StatusUnprocessableEntity, err)
		return
	}
	events, err := s.store.SearchLogs(request.Context(), query)
	if err != nil {
		s.writeError(writer, request, http.StatusBadGateway, err)
		return
	}
	writeJSON(writer, http.StatusOK, map[string]any{
		"events": events,
		"count":  len(events),
	})
}

func (s *Server) dependencies(writer http.ResponseWriter, request *http.Request) {
	service := request.PathValue("service")
	if !validService(service) {
		s.writeError(writer, request, http.StatusUnprocessableEntity, errors.New("invalid service name"))
		return
	}
	writeJSON(writer, http.StatusOK, map[string]any{
		"service": service,
		"edges":   s.store.Dependencies(service),
		// Both current stores use the static fixture catalog, not observed traces.
		"topology_kind": "static_fixture_catalog",
	})
}

func (s *Server) observedDependencies(writer http.ResponseWriter, request *http.Request) {
	service := request.PathValue("service")
	if !validService(service) || request.URL.RawQuery != "" {
		s.writeError(writer, request, http.StatusUnprocessableEntity, errors.New("invalid observed dependency request"))
		return
	}
	store, supported := s.store.(interface {
		ObservedDependencies(context.Context, TraceSearch) (ObservedTopology, error)
	})
	if !supported {
		s.writeError(writer, request, http.StatusNotImplemented, errors.New("observed dependencies require a trace backend"))
		return
	}
	end := time.Now().UTC()
	result, err := store.ObservedDependencies(request.Context(), TraceSearch{Service: service, Start: end.Add(-30 * time.Minute), End: end, Limit: 8})
	if err != nil {
		s.writeError(writer, request, http.StatusBadGateway, errors.New("observed dependency query unavailable"))
		return
	}
	writeJSON(writer, http.StatusOK, result)
}

func (s *Server) deployments(writer http.ResponseWriter, request *http.Request) {
	service := request.PathValue("service")
	if !validService(service) {
		s.writeError(writer, request, http.StatusUnprocessableEntity, errors.New("invalid service name"))
		return
	}
	writeJSON(writer, http.StatusOK, map[string]any{
		"service":     service,
		"deployments": s.store.Deployments(service),
		"source":      "synthetic-deployment-catalog",
		"data_origin": "synthetic",
	})
}

func (s *Server) authenticate(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path == "/health" {
			next.ServeHTTP(writer, request)
			return
		}
		provided := strings.TrimPrefix(request.Header.Get("Authorization"), "Bearer ")
		if provided == "" ||
			subtle.ConstantTimeCompare([]byte(provided), []byte(s.token)) != 1 {
			s.writeError(writer, request, http.StatusUnauthorized, errors.New("unauthorized"))
			return
		}
		next.ServeHTTP(writer, request)
	})
}

func (s *Server) rateLimit(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		host, _, err := net.SplitHostPort(request.RemoteAddr)
		if err != nil {
			host = request.RemoteAddr
		}
		if !s.rateLimiter.Allow(host) {
			writer.Header().Set("Retry-After", "60")
			s.writeError(writer, request, http.StatusTooManyRequests, errors.New("rate limit exceeded"))
			return
		}
		next.ServeHTTP(writer, request)
	})
}

func (s *Server) trace(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		traceID := request.Header.Get("X-Trace-ID")
		if traceID == "" {
			traceID = "gw-" + strconv.FormatInt(time.Now().UnixNano(), 36)
		}
		writer.Header().Set("X-Trace-ID", traceID)
		ctx := context.WithValue(request.Context(), traceIDKey, traceID)
		next.ServeHTTP(writer, request.WithContext(ctx))
	})
}

func (s *Server) audit(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		started := time.Now()
		next.ServeHTTP(writer, request)
		s.logger.Info("telemetry_query",
			"trace_id", traceID(request),
			"method", request.Method,
			"path", request.URL.Path,
			"duration_ms", time.Since(started).Milliseconds(),
		)
	})
}

func (s *Server) writeError(writer http.ResponseWriter, request *http.Request, status int, err error) {
	writeJSON(writer, status, ErrorResponse{Error: err.Error(), TraceID: traceID(request)})
}

func traceID(request *http.Request) string {
	value, _ := request.Context().Value(traceIDKey).(string)
	return value
}

func decodeJSON(request *http.Request, destination any) error {
	decoder := json.NewDecoder(http.MaxBytesReader(nil, request.Body, 1<<20))
	decoder.DisallowUnknownFields()
	return decoder.Decode(destination)
}

func writeJSON(writer http.ResponseWriter, status int, value any) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(value)
}

func validateMetricQuery(query MetricQuery) error {
	if !validService(query.Service) {
		return errors.New("invalid service name")
	}
	if query.Metric == "" || len(query.Metric) > 120 {
		return errors.New("metric is required and must be at most 120 characters")
	}
	if query.Start.IsZero() || query.End.IsZero() || !query.Start.Before(query.End) {
		return errors.New("start must be before end")
	}
	if query.End.Sub(query.Start) > 24*time.Hour {
		return errors.New("query window cannot exceed 24 hours")
	}
	if query.StepSecs < 1 || query.StepSecs > 3600 {
		return errors.New("step_seconds must be between 1 and 3600")
	}
	return nil
}

func validateLogSearch(query LogSearch) error {
	if !validService(query.Service) {
		return errors.New("invalid service name")
	}
	if len(query.Query) > 500 {
		return errors.New("query must be at most 500 characters")
	}
	if query.Start.IsZero() || query.End.IsZero() || !query.Start.Before(query.End) {
		return errors.New("start must be before end")
	}
	if query.Limit < 1 || query.Limit > 1000 {
		return errors.New("limit must be between 1 and 1000")
	}
	return nil
}

func validateTraceSearch(query TraceSearch) error {
	if !validService(query.Service) {
		return errors.New("invalid service name")
	}
	if query.Start.IsZero() || query.End.IsZero() || !query.Start.Before(query.End) {
		return errors.New("start must be before end")
	}
	if query.End.Sub(query.Start) > 24*time.Hour {
		return errors.New("query window cannot exceed 24 hours")
	}
	if query.Limit < 1 || query.Limit > 100 {
		return errors.New("limit must be between 1 and 100")
	}
	return nil
}

func validService(service string) bool {
	if len(service) < 2 || len(service) > 80 {
		return false
	}
	for _, character := range service {
		if (character < 'a' || character > 'z') &&
			(character < '0' || character > '9') &&
			character != '-' {
			return false
		}
	}
	return true
}
