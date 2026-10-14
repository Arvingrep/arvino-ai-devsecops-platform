package k8s_test

import rego.v1

import data.k8s.argocd
import data.k8s.nosecrets
import data.k8s.secrets
import data.k8s.workloads

good_deploy := {
	"kind": "Deployment",
	"metadata": {"name": "x"},
	"spec": {"template": {"spec": {
		"automountServiceAccountToken": false,
		"securityContext": {"runAsNonRoot": true},
		"containers": [{
			"name": "c",
			"image": "ghcr.io/arvino/orchestrator:0.1.0",
			"securityContext": {"allowPrivilegeEscalation": false, "capabilities": {"drop": ["ALL"]}},
			"resources": {"requests": {"cpu": "1m"}, "limits": {"cpu": "1", "memory": "1Mi"}},
		}],
	}}},
}

test_good_deployment_passes if {
	count(workloads.deny) == 0 with input as good_deploy
}

test_latest_and_untagged_images_denied if {
	bad1 := json.patch(good_deploy, [{"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": "nginx:latest"}])
	bad2 := json.patch(good_deploy, [{"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": "nginx"}])
	count(workloads.deny) > 0 with input as bad1
	count(workloads.deny) > 0 with input as bad2
}

test_digest_pinned_image_ok if {
	ok := json.patch(good_deploy, [{"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": "nginx@sha256:abc"}])
	count(workloads.deny) == 0 with input as ok
}

test_registry_port_is_not_a_tag if {
	bad := json.patch(good_deploy, [{"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": "localhost:5000/app"}])
	count(workloads.deny) > 0 with input as bad
}

test_privileged_and_escalation_denied if {
	bad := json.patch(good_deploy, [
		{"op": "replace", "path": "/spec/template/spec/containers/0/securityContext/allowPrivilegeEscalation", "value": true},
		{"op": "add", "path": "/spec/template/spec/containers/0/securityContext/privileged", "value": true},
	])
	count(workloads.deny) >= 2 with input as bad
}

test_missing_limits_and_root_denied if {
	bad := json.patch(good_deploy, [
		{"op": "remove", "path": "/spec/template/spec/containers/0/resources/limits"},
		{"op": "replace", "path": "/spec/template/spec/securityContext/runAsNonRoot", "value": false},
	])
	count(workloads.deny) >= 3 with input as bad
}

test_host_namespaces_and_hostpath_denied if {
	bad := json.patch(good_deploy, [
		{"op": "add", "path": "/spec/template/spec/hostNetwork", "value": true},
		{"op": "add", "path": "/spec/template/spec/volumes", "value": [{"name": "h", "hostPath": {"path": "/"}}]},
	])
	count(workloads.deny) >= 2 with input as bad
}

test_secret_placeholders_only if {
	count(secrets.deny) == 0 with input as {"kind": "Secret", "metadata": {"name": "s"}, "stringData": {"k": "REPLACE_ME"}}
	count(secrets.deny) > 0 with input as {"kind": "Secret", "metadata": {"name": "s"}, "stringData": {"k": "hunter2"}}
	count(secrets.deny) > 0 with input as {"kind": "Secret", "metadata": {"name": "s"}, "data": {"k": base64.encode("hunter2")}}
	count(secrets.deny) == 0 with input as {"kind": "Secret", "metadata": {"name": "s"}, "data": {"k": base64.encode("REPLACE_ME")}}
}

test_no_secrets_in_rendered_overlays if {
	count(nosecrets.deny) > 0 with input as {"kind": "Secret", "metadata": {"name": "s"}}
	count(nosecrets.deny) == 0 with input as {"kind": "ConfigMap", "metadata": {"name": "s"}}
}

test_production_application_must_not_auto_sync if {
	prod := {"kind": "Application", "metadata": {"name": "arvino-production", "annotations": {}}, "spec": {"syncPolicy": {"automated": {}}}}
	dev := {"kind": "Application", "metadata": {"name": "arvino-dev", "annotations": {}}, "spec": {"syncPolicy": {"automated": {}}}}
	count(argocd.deny) > 0 with input as prod
	count(argocd.deny) == 0 with input as dev
}

test_appproject_wildcards_denied if {
	count(argocd.deny) > 0 with input as {"kind": "AppProject", "metadata": {"name": "p"}, "spec": {"clusterResourceWhitelist": [{"group": "*", "kind": "*"}], "destinations": []}}
	count(argocd.deny) > 0 with input as {"kind": "AppProject", "metadata": {"name": "p"}, "spec": {"clusterResourceWhitelist": [], "destinations": [{"namespace": "*"}]}}
}
