package k8s.nosecrets

import rego.v1

# Rendered overlays must not contain Secrets at all: they come from outside GitOps.
deny contains msg if {
	input.kind == "Secret"
	msg := sprintf("Secret/%s must not be part of a rendered overlay (provision via External Secrets/SOPS/kubectl)", [input.metadata.name])
}
