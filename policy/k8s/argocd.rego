# Conftest policy: Argo CD guardrails.
#   conftest test --policy policy/k8s --namespace k8s.argocd deploy/argocd/*.yaml
package k8s.argocd

is_production(app) if app.metadata.annotations["arvino.dev/environment"] == "production"

is_production(app) if contains(app.metadata.name, "production")

deny contains msg if {
	input.kind == "Application"
	is_production(input)
	input.spec.syncPolicy.automated
	msg := sprintf("Application/%s: production must not have automated sync (manual owner sync only)", [input.metadata.name])
}

deny contains msg if {
	input.kind == "AppProject"
	some r in input.spec.clusterResourceWhitelist
	r.kind == "*"
	msg := sprintf("AppProject/%s: wildcard clusterResourceWhitelist is not allowed", [input.metadata.name])
}

deny contains msg if {
	input.kind == "AppProject"
	some r in input.spec.clusterResourceWhitelist
	r.group == "*"
	msg := sprintf("AppProject/%s: wildcard clusterResourceWhitelist is not allowed", [input.metadata.name])
}

deny contains msg if {
	input.kind == "AppProject"
	some d in input.spec.destinations
	d.namespace == "*"
	msg := sprintf("AppProject/%s: wildcard destination namespace is not allowed", [input.metadata.name])
}
