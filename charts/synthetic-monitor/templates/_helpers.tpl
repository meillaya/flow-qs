{{/*
Expand the name of the chart.
*/}}
{{- define "syntheticmonitor.name" -}}
{{- default .Chart.Name (index .Values "svc-template" "serviceName") | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "syntheticmonitor.fullname" -}}
{{- $svc := index .Values "svc-template" -}}
{{- if $svc.serviceName }}
{{- $svc.serviceName | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "syntheticmonitor.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "syntheticmonitor.labels" -}}
helm.sh/chart: {{ include "syntheticmonitor.chart" . }}
{{ include "syntheticmonitor.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: monitor
{{- end }}

{{/*
Selector labels
*/}}
{{- define "syntheticmonitor.selectorLabels" -}}
app.kubernetes.io/name: {{ include "syntheticmonitor.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
