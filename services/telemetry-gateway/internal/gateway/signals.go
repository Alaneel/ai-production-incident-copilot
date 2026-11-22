package gateway

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"regexp"
	"sort"
	"strings"
)

// A SignalProfile maps the orchestrator's canonical signals onto one
// environment's telemetry naming. The Agent and MCP interfaces never change:
// only the operator-owned PromQL/LogQL/TraceQL templates differ per stack.
//
// Templates are trusted operator configuration. The only runtime substitution
// is {{service}}, and service names are validated to [a-z0-9-] before use, so a
// request cannot inject query syntax.
type SignalProfile struct {
	Name           string
	metrics        map[string]metricDefinition
	logSelector    string
	traceAttribute string
	// declared is nil when the built-in fixture catalog should be used.
	declared map[string][]Dependency
}

const servicePlaceholder = "{{service}}"

var (
	traceAttributePattern = regexp.MustCompile(`^(resource|span)\.[A-Za-z0-9_.]{1,100}$`)
	logSelectorPattern    = regexp.MustCompile(`^\{[^{}]*\{\{service\}\}[^{}]*\}$`)
	profileNamePattern    = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{1,63}$`)
)

// metricAliases resolves the gateway's historical request names onto the
// canonical signal a profile configures.
var metricAliases = map[string]string{
	"error_rate":          "request_error_rate",
	"db_pool_utilization": "db_pool_active_ratio",
	"cpu_load":            "cpu_utilization",
}

// DefaultSignalProfile is the fault-lab naming used by the checked-in demo and
// acceptance stacks. Behaviour is unchanged when no profile file is supplied.
func DefaultSignalProfile() *SignalProfile {
	return &SignalProfile{
		Name:           "fault-lab",
		metrics:        allowedMetrics,
		logSelector:    `{service="{{service}}"}`,
		traceAttribute: "resource.service.name",
	}
}

type signalProfileFile struct {
	Name    string `json:"name"`
	Metrics map[string]struct {
		PromQL string `json:"promql"`
		Unit   string `json:"unit,omitempty"`
	} `json:"metrics"`
	LogSelector           string              `json:"log_selector"`
	TraceServiceAttribute string              `json:"trace_service_attribute"`
	Dependencies          map[string][]string `json:"dependencies,omitempty"`
}

// LoadSignalProfile reads and strictly validates a JSON signal profile.
func LoadSignalProfile(path string) (*SignalProfile, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read signal profile: %w", err)
	}
	return ParseSignalProfile(raw)
}

func ParseSignalProfile(raw []byte) (*SignalProfile, error) {
	if len(raw) > 256<<10 {
		return nil, errors.New("signal profile exceeds 256 KiB")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var file signalProfileFile
	if err := decoder.Decode(&file); err != nil {
		return nil, fmt.Errorf("parse signal profile: %w", err)
	}
	if decoder.More() {
		return nil, errors.New("parse signal profile: trailing content")
	}
	if !profileNamePattern.MatchString(file.Name) {
		return nil, errors.New("profile name must be 2-64 lowercase letters, digits or hyphens")
	}
	if len(file.Metrics) == 0 {
		return nil, errors.New("profile must configure at least one metric")
	}
	profile := &SignalProfile{Name: file.Name, metrics: map[string]metricDefinition{}}
	for name, entry := range file.Metrics {
		if _, alias := metricAliases[name]; alias {
			return nil, fmt.Errorf("metric %q: use canonical name %q", name, metricAliases[name])
		}
		builtin, known := allowedMetrics[name]
		if !known {
			return nil, fmt.Errorf("metric %q is not a known signal; known: %s", name, strings.Join(KnownSignals(), ", "))
		}
		if entry.Unit != "" && entry.Unit != builtin.unit {
			return nil, fmt.Errorf("metric %q must return %s, profile declares %s", name, builtin.unit, entry.Unit)
		}
		template := strings.TrimSpace(entry.PromQL)
		if !strings.Contains(template, servicePlaceholder) {
			return nil, fmt.Errorf("metric %q: promql must reference %s", name, servicePlaceholder)
		}
		if len(template) > 2000 {
			return nil, fmt.Errorf("metric %q: promql exceeds 2000 characters", name)
		}
		profile.metrics[name] = metricDefinition{query: expand(template), unit: builtin.unit}
	}
	profile.logSelector = strings.TrimSpace(file.LogSelector)
	if profile.logSelector == "" {
		profile.logSelector = `{service="{{service}}"}`
	}
	if !logSelectorPattern.MatchString(profile.logSelector) || len(profile.logSelector) > 300 {
		return nil, errors.New(`log_selector must be one LogQL stream selector containing {{service}}, e.g. {app="{{service}}"}`)
	}
	profile.traceAttribute = file.TraceServiceAttribute
	if profile.traceAttribute == "" {
		profile.traceAttribute = "resource.service.name"
	}
	if !traceAttributePattern.MatchString(profile.traceAttribute) {
		return nil, errors.New("trace_service_attribute must look like resource.service.name")
	}
	if file.Dependencies != nil {
		profile.declared = map[string][]Dependency{}
		edges := 0
		for source, targets := range file.Dependencies {
			if !validService(source) {
				return nil, fmt.Errorf("dependencies: invalid service name %q", source)
			}
			for _, target := range targets {
				if !validService(target) || target == source {
					return nil, fmt.Errorf("dependencies: invalid target %q for %q", target, source)
				}
				edge := Dependency{Source: source, Target: target, Protocol: "declared"}
				profile.declared[source] = append(profile.declared[source], edge)
				profile.declared[target] = append(profile.declared[target], edge)
				edges++
			}
		}
		if edges > 500 {
			return nil, errors.New("dependencies: at most 500 edges are supported")
		}
	}
	return profile, nil
}

func expand(template string) func(string) string {
	return func(service string) string {
		// validService has already restricted service to [a-z0-9-].
		return strings.ReplaceAll(template, servicePlaceholder, service)
	}
}

// KnownSignals lists the canonical signal names a profile may configure.
func KnownSignals() []string {
	names := make([]string, 0, len(allowedMetrics))
	for name := range allowedMetrics {
		if _, alias := metricAliases[name]; !alias {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	return names
}

// ConfiguredSignals lists the canonical signals this profile can answer.
func (p *SignalProfile) ConfiguredSignals() []string {
	names := make([]string, 0, len(p.metrics))
	for name := range p.metrics {
		if _, alias := metricAliases[name]; !alias {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	return names
}

// metric resolves a request name. known reports whether the name is a signal
// at all; ok reports whether this profile configures it.
func (p *SignalProfile) metric(name string) (definition metricDefinition, known bool, ok bool) {
	if definition, ok = p.metrics[name]; ok {
		return definition, true, true
	}
	if canonical, alias := metricAliases[name]; alias {
		if definition, ok = p.metrics[canonical]; ok {
			return definition, true, true
		}
	}
	builtin, known := allowedMetrics[name]
	return metricDefinition{unit: builtin.unit}, known, false
}

func (p *SignalProfile) logQuery(service string) string {
	return strings.ReplaceAll(p.logSelector, servicePlaceholder, service)
}

func (p *SignalProfile) traceQuery(service string) string {
	return fmt.Sprintf(`{ %s = %q } with (most_recent=true)`, p.traceAttribute, service)
}

// DeclaresDependencies reports whether the operator supplied a catalog.
func (p *SignalProfile) DeclaresDependencies() bool { return p.declared != nil }
