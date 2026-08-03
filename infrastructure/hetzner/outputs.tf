output "cluster_name" {
  description = "Cluster name, as the node-group prefix the autoscaler matches on."
  value       = module.kube_hetzner.cluster_name
}

output "kubeconfig" {
  description = <<-EOT
    Admin kubeconfig.

    Sensitive, and it stays that way: no kubeconfig is ever placed in a CI
    system in this kit. Read it locally, use it locally.
  EOT
  value       = module.kube_hetzner.kubeconfig
  sensitive   = true
}

output "control_planes_public_ipv4" {
  description = "Public IPv4 addresses of the control plane nodes. Three of them, or the HA claim is not true."
  value       = module.kube_hetzner.control_planes_public_ipv4
}

output "agents_public_ipv4" {
  description = "Public IPv4 addresses of the fixed agent nodes. Autoscaled nodes are not here — they do not exist at plan time."
  value       = module.kube_hetzner.agents_public_ipv4
}

# -----------------------------------------------------------------------------
# Priority expander ConfigMap.
#
# Generated from the same pool list that configures the autoscaler, so it can
# never name a group that does not exist or omit one that does. Uncommenting the
# fallback pool in main.tf updates this output in the same edit — which is the
# entire point of generating it rather than writing it out by hand somewhere.
#
# Why this is an output and not a resource: applying it would require a
# kubernetes provider at the root configured from this module's own output. That
# provider configuration is unknown at plan time on a fresh cluster, so the very
# first `tofu plan` fails and the fix is a two-phase apply. Shipping that trap
# into a kit whose first command is `tofu init && tofu apply` is worse than one
# documented kubectl line. The delivery layer is the other honest home for it.
#
# Applying it late is safe: upstream skips the priority expander entirely when
# the ConfigMap is missing or malformed, and falls through to the next expander
# (least-waste, per main.tf). Missing config degrades the choice, it does not
# break scaling.
#
#   tofu output -raw autoscaler_priority_expander_manifest | kubectl apply -f -
#
# Not verified against a running cluster.
# -----------------------------------------------------------------------------
output "autoscaler_priority_expander_manifest" {
  description = "ConfigMap manifest telling cluster-autoscaler which nodepool to prefer. Apply with kubectl, or commit it to the delivery layer."
  value       = local.autoscaler_priority_expander_manifest
}
