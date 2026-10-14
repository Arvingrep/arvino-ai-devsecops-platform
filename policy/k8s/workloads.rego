# Conftest policy: workload hardening for the manifests in this repo.
#   conftest test --policy policy/k8s --namespace k8s.workloads <rendered.yaml>
package k8s.workloads

pod_controllers := {"Deployment", "StatefulSet", "DaemonSet", "Job", "ReplicaSet"}

pod_spec := spec if {
	input.kind in pod_controllers
	spec := input.spec.template.spec
}

containers contains c if {
	some c in pod_spec.containers
}

containers contains c if {
	some c in pod_spec.initContainers
}

deny contains msg if {
	some c in containers
	not pinned(c.image)
	msg := sprintf("%s/%s: container %q uses unpinned image %q (tag required, never :latest)", [input.kind, input.metadata.name, c.name, c.image])
}

pinned(image) if {
	contains(image, "@sha256:")
}

pinned(image) if {
	parts := split(image, ":")
	count(parts) > 1
	tag := parts[count(parts) - 1]
	not contains(tag, "/")
	tag != "latest"
}

deny contains msg if {
	some c in containers
	not c.securityContext.allowPrivilegeEscalation == false
	msg := sprintf("%s/%s: container %q must set allowPrivilegeEscalation: false", [input.kind, input.metadata.name, c.name])
}

deny contains msg if {
	some c in containers
	c.securityContext.privileged == true
	msg := sprintf("%s/%s: container %q must not be privileged", [input.kind, input.metadata.name, c.name])
}

deny contains msg if {
	some c in containers
	not drops_all(c)
	msg := sprintf("%s/%s: container %q must drop ALL capabilities", [input.kind, input.metadata.name, c.name])
}

drops_all(c) if "ALL" in c.securityContext.capabilities.drop

deny contains msg if {
	some c in containers
	not runs_as_non_root(c)
	msg := sprintf("%s/%s: container %q must run as non-root (runAsNonRoot)", [input.kind, input.metadata.name, c.name])
}

runs_as_non_root(c) if c.securityContext.runAsNonRoot == true

runs_as_non_root(_) if pod_spec.securityContext.runAsNonRoot == true

deny contains msg if {
	some c in containers
	not c.resources.limits.memory
	msg := sprintf("%s/%s: container %q needs a memory limit", [input.kind, input.metadata.name, c.name])
}

deny contains msg if {
	some c in containers
	not c.resources.limits.cpu
	msg := sprintf("%s/%s: container %q needs a cpu limit", [input.kind, input.metadata.name, c.name])
}

deny contains msg if {
	some c in containers
	not c.resources.requests
	msg := sprintf("%s/%s: container %q needs resource requests", [input.kind, input.metadata.name, c.name])
}

deny contains msg if {
	some key in ["hostNetwork", "hostPID", "hostIPC"]
	pod_spec[key] == true
	msg := sprintf("%s/%s: %s is not allowed", [input.kind, input.metadata.name, key])
}

deny contains msg if {
	some v in pod_spec.volumes
	v.hostPath
	msg := sprintf("%s/%s: hostPath volumes are not allowed", [input.kind, input.metadata.name])
}

deny contains msg if {
	pod_spec
	not pod_spec.automountServiceAccountToken == false
	msg := sprintf("%s/%s: set automountServiceAccountToken: false (no workload here talks to the Kubernetes API)", [input.kind, input.metadata.name])
}
