{{- define "runsigil.name" -}}runsigil{{- end -}}
{{- define "runsigil.labels" -}}
app.kubernetes.io/name: runsigil
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

