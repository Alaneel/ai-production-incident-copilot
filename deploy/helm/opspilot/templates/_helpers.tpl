{{- define "opspilot.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 40 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 40 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "opspilot.name" -}}
{{- printf "%s-%s" (include "opspilot.fullname" .root) .component -}}
{{- end -}}

{{- define "opspilot.labels" -}}
app.kubernetes.io/name: {{ .component }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/part-of: opspilot
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version }}
{{- end -}}

{{- define "opspilot.selector" -}}
app.kubernetes.io/name: {{ .component }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end -}}

{{- define "opspilot.image" -}}
{{- printf "%s/opspilot-%s:%s" .root.Values.image.registry .component (default .root.Chart.AppVersion .root.Values.image.tag) -}}
{{- end -}}

{{- define "opspilot.secretName" -}}
{{- default (printf "%s-credentials" (include "opspilot.fullname" .)) .Values.auth.existingSecret -}}
{{- end -}}

{{- define "opspilot.hasDatabase" -}}
{{- if or .Values.postgres.enabled .Values.externalDatabase.url .Values.auth.existingSecret -}}true{{- end -}}
{{- end -}}

{{- define "opspilot.liveTelemetry" -}}
{{- $t := .Values.telemetry -}}
{{- $set := 0 -}}
{{- range (list $t.prometheusUrl $t.lokiUrl $t.tempoUrl) -}}{{- if . -}}{{- $set = add1 $set -}}{{- end -}}{{- end -}}
{{- if and (gt $set 0) (lt $set 3) -}}
{{- fail "telemetry.prometheusUrl, telemetry.lokiUrl and telemetry.tempoUrl must be set together (or all left empty for demo fixtures)" -}}
{{- end -}}
{{- if eq $set 3 -}}true{{- end -}}
{{- end -}}

{{- define "opspilot.podSecurity" -}}
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "opspilot.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}

{{- define "opspilot.secretEnv" -}}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ include "opspilot.secretName" .root }}
      key: {{ .key }}
{{- if .optional }}
      optional: true
{{- end }}
{{- end -}}
