{{/*
Expand the name of the chart.
*/}}
{{- define "incidentengine.name" -}}
{{- default .Chart.Name .Values.serviceName | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "incidentengine.fullname" -}}
{{- if .Values.serviceName }}
{{- .Values.serviceName | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "incidentengine.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "incidentengine.labels" -}}
helm.sh/chart: {{ include "incidentengine.chart" . }}
{{ include "incidentengine.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: incident-engine
{{- end }}

{{/*
Selector labels
*/}}
{{- define "incidentengine.selectorLabels" -}}
app.kubernetes.io/name: {{ include "incidentengine.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
ServiceAccount name to use.
*/}}
{{- define "incidentengine.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "incidentengine.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}