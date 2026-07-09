{{/*
Expand the name of the chart.
*/}}
{{- define "observability-platform.name" -}}
{{- default .Chart.Name .Values.serviceName | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "observability-platform.fullname" -}}
{{- if .Values.serviceName }}
{{- .Values.serviceName | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "observability-platform.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "observability-platform.labels" -}}
helm.sh/chart: {{ include "observability-platform.chart" . }}
{{ include "observability-platform.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: service
{{- end }}

{{/*
Selector labels
*/}}
{{- define "observability-platform.selectorLabels" -}}
app.kubernetes.io/name: {{ include "observability-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "observability-platform.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "observability-platform.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}
