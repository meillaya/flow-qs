{{/*
Common helpers for the checkout-service chart. Reuses the umbrella chart
template names so the chart plays nicely with `helm template` from the
top-level flow-qs umbrella.
*/}}

{{- define "observability-platform.name" -}}
{{- default .Chart.Name .Values.serviceName | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "observability-platform.fullname" -}}
{{- $name := default .Chart.Name .Values.serviceName -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "observability-platform.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "observability-platform.labels" -}}
helm.sh/chart: {{ include "observability-platform.chart" . }}
app.kubernetes.io/name: {{ include "observability-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/component: orchestrator
app.kubernetes.io/part-of: flow-qs
{{- end -}}

{{- define "observability-platform.selectorLabels" -}}
app.kubernetes.io/name: {{ include "observability-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "observability-platform.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "observability-platform.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}
