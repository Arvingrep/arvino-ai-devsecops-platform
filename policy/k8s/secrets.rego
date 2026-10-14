# Conftest policy: no real secrets in Git.
#   conftest test --policy policy/k8s --namespace k8s.secrets deploy/secrets-examples
#   conftest test --policy policy/k8s --namespace k8s.nosecrets <rendered overlay>
package k8s.secrets

deny contains msg if {
	input.kind == "Secret"
	some k, v in input.stringData
	not startswith(v, "REPLACE_ME")
	msg := sprintf("Secret/%s key %q holds a non-placeholder value; only REPLACE_ME placeholders may be committed", [input.metadata.name, k])
}

deny contains msg if {
	input.kind == "Secret"
	some k, v in input.data
	not startswith(base64.decode(v), "REPLACE_ME")
	msg := sprintf("Secret/%s key %q holds a non-placeholder value; only REPLACE_ME placeholders may be committed", [input.metadata.name, k])
}
