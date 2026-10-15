package gateway

import (
	"context"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"
)

// TraceCall proves observed synchronous parentage, not incident causation.
type TraceCall struct {
	Source       string    `json:"source"`
	Target       string    `json:"target"`
	TraceID      string    `json:"trace_id"`
	ClientSpanID string    `json:"client_span_id"`
	ServerSpanID string    `json:"server_span_id"`
	ObservedAt   time.Time `json:"observed_at"`
}

type ObservedTopology struct {
	Service      string      `json:"service"`
	Calls        []TraceCall `json:"calls"`
	Source       string      `json:"source"`
	Start        time.Time   `json:"start"`
	End          time.Time   `json:"end"`
	TracesRead   int         `json:"traces_read"`
	TracesFailed int         `json:"traces_failed"`
	CallsOmitted int         `json:"calls_omitted"`
	Coverage     string      `json:"coverage"`
}

type otlpSpan struct {
	TraceID string          `json:"traceId"`
	ID      string          `json:"spanId"`
	Parent  string          `json:"parentSpanId"`
	Kind    json.RawMessage `json:"kind"`
	Start   string          `json:"startTimeUnixNano"`
	End     string          `json:"endTimeUnixNano"`
}

type spanScope struct {
	Spans []otlpSpan `json:"spans"`
}

type resourceSpans struct {
	Resource struct {
		Attributes []struct {
			Key   string `json:"key"`
			Value struct {
				String string `json:"stringValue"`
			} `json:"value"`
		} `json:"attributes"`
	} `json:"resource"`
	Scopes []spanScope `json:"scopeSpans"`
	Legacy []spanScope `json:"instrumentationLibrarySpans"`
}

type traceDocument struct {
	Resources []resourceSpans `json:"resourceSpans"`
	Batches   []resourceSpans `json:"batches"`
}

func canonicalID(raw string, size int) string {
	var bytes []byte
	var err error
	if len(raw) == size*2 {
		bytes, err = hex.DecodeString(raw)
	} else {
		bytes, err = base64.StdEncoding.DecodeString(raw)
	}
	if err != nil || len(bytes) != size {
		return ""
	}
	zero := true
	for _, value := range bytes {
		zero = zero && value == 0
	}
	if zero {
		return ""
	}
	return hex.EncodeToString(bytes)
}

// Tempo search may omit leading zeroes in hex trace IDs. Normalize only
// that boundary; OTLP span IDs retain their exact byte-width validation.
func canonicalTempoSearchID(raw string) string {
	if len(raw) == 0 || len(raw) > 32 {
		return ""
	}
	for _, digit := range raw {
		if !(digit >= '0' && digit <= '9' || digit >= 'a' && digit <= 'f' || digit >= 'A' && digit <= 'F') {
			return ""
		}
	}
	return canonicalID(strings.Repeat("0", 32-len(raw))+raw, 16)
}

func spanKind(raw json.RawMessage, name string, value int) bool {
	var number int
	if json.Unmarshal(raw, &number) == nil {
		return number == value
	}
	var text string
	return json.Unmarshal(raw, &text) == nil && text == name
}

// No temporal adjacency, service-name co-occurrence or span links are inferred as calls.
func extractTraceCalls(raw json.RawMessage, traceID string, query TraceSearch) ([]TraceCall, error) {
	traceID = canonicalID(traceID, 16)
	if traceID == "" {
		return nil, errors.New("invalid trace identifier")
	}
	var document traceDocument
	if err := json.Unmarshal(raw, &document); err != nil {
		return nil, errors.New("invalid trace document")
	}
	if document.Resources != nil && document.Batches != nil {
		return nil, errors.New("ambiguous trace resource format")
	}
	resources := document.Resources
	if resources == nil {
		resources = document.Batches
	}
	if resources == nil {
		return nil, errors.New("missing trace resources")
	}
	type node struct {
		service string
		span    otlpSpan
		start   time.Time
	}
	nodes, ambiguous := map[string]node{}, map[string]bool{}
	count := 0
	for _, resource := range resources {
		service, attributes := "", 0
		for _, attribute := range resource.Resource.Attributes {
			if attribute.Key == "service.name" {
				attributes++
				service = attribute.Value.String
			}
		}
		if attributes != 1 || !validService(service) {
			service = ""
		}
		if resource.Scopes != nil && resource.Legacy != nil {
			return nil, errors.New("ambiguous trace scope format")
		}
		scopes := resource.Scopes
		if scopes == nil {
			scopes = resource.Legacy
		}
		for _, scope := range scopes {
			for _, span := range scope.Spans {
				count++
				if count > 2048 {
					return nil, errors.New("trace span budget exceeded")
				}
				if canonicalID(span.TraceID, 16) != traceID {
					continue
				}
				id := canonicalID(span.ID, 8)
				if id == "" {
					continue
				}
				if _, exists := nodes[id]; exists {
					ambiguous[id] = true
					continue
				}
				start, firstErr := strconv.ParseInt(span.Start, 10, 64)
				end, lastErr := strconv.ParseInt(span.End, 10, 64)
				if firstErr != nil || lastErr != nil || start <= 0 || end < start || start > query.End.UnixNano() || end < query.Start.UnixNano() {
					ambiguous[id] = true
					continue
				}
				span.Parent = canonicalID(span.Parent, 8)
				nodes[id] = node{service: service, span: span, start: time.Unix(0, start).UTC()}
			}
		}
	}
	calls := []TraceCall{}
	for id, child := range nodes {
		parent, found := nodes[child.span.Parent]
		if !found || ambiguous[id] || ambiguous[child.span.Parent] || child.service == "" || parent.service == "" || child.service == parent.service {
			continue
		}
		if child.service != query.Service && parent.service != query.Service {
			continue
		}
		if !spanKind(child.span.Kind, "SPAN_KIND_SERVER", 2) || !spanKind(parent.span.Kind, "SPAN_KIND_CLIENT", 3) {
			continue
		}
		seen, cursor, safe := map[string]bool{}, id, true
		for depth := 0; cursor != ""; depth++ {
			if seen[cursor] || ambiguous[cursor] || depth >= 256 {
				safe = false
				break
			}
			seen[cursor] = true
			ancestor, exists := nodes[cursor]
			if !exists {
				break // An absent remote ancestor does not invalidate this exact pair.
			}
			cursor = ancestor.span.Parent
		}
		if safe {
			calls = append(calls, TraceCall{Source: parent.service, Target: child.service, TraceID: traceID,
				ClientSpanID: child.span.Parent, ServerSpanID: id, ObservedAt: child.start})
		}
	}
	sort.Slice(calls, func(i, j int) bool { return calls[i].ServerSpanID < calls[j].ServerSpanID })
	return calls, nil
}

func (s *ObservabilityStore) ObservedDependencies(ctx context.Context, query TraceSearch) (ObservedTopology, error) {
	result := ObservedTopology{Service: query.Service, Calls: []TraceCall{}, Source: "tempo_client_server_parentage",
		Start: query.Start, End: query.End, Coverage: "sampled_not_exhaustive"}
	query.Limit = 8
	if err := validateTraceSearch(query); err != nil {
		return result, err
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	traces, err := s.SearchTraces(ctx, query)
	if err != nil {
		return result, errors.New("dependency trace search unavailable")
	}
	seen := map[string]bool{}
	for _, summary := range traces {
		if result.TracesRead+result.TracesFailed >= 8 {
			break
		}
		id := canonicalID(summary.TraceID, 16)
		if id == "" || seen[id] {
			continue
		}
		seen[id] = true
		parameters := url.Values{"start": {strconv.FormatInt(query.Start.Unix(), 10)}, "end": {strconv.FormatInt(query.End.Unix(), 10)}}
		var raw json.RawMessage
		if s.getJSON(ctx, s.tempoURL+"/api/traces/"+id+"?"+parameters.Encode(), &raw) != nil {
			result.TracesFailed++
			continue
		}
		calls, parseErr := extractTraceCalls(raw, id, query)
		if parseErr != nil {
			result.TracesFailed++
			continue
		}
		result.TracesRead++
		remaining := 64 - len(result.Calls)
		if len(calls) > remaining {
			result.CallsOmitted += len(calls) - remaining
			calls = calls[:remaining]
		}
		result.Calls = append(result.Calls, calls...)
	}
	if result.TracesFailed > 0 {
		result.Coverage = "partial_sample_not_exhaustive"
	}
	return result, nil
}
