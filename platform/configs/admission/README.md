# Admission

One policy. It is the only place in the tenant contract where something is rejected rather than generated and reported.

## Hostname authorisation

A tenant may only create routes for hostnames under domains declared on its own namespace.

```yaml
# tenants/acme/namespace.yaml
metadata:
  annotations:
    platform.example.com/domains: "acme.example.com,acme.example.net"
```

`acme.example.com`, `www.acme.example.com` and `*.acme.example.com` are accepted. `example.com`, `other.example.com` and `notacme.example.com` are rejected at admission, before the object exists and before any certificate is requested.

A namespace with no annotation may claim no hostnames at all.

### Why this and not per-namespace issuers

With a shared issuer and hostname-driven issuance, any tenant able to create a route can obtain a certificate for **any** hostname — another tenant's included — and exhaust shared issuance limits doing it.

Giving each tenant its own issuer does not close that. An issuer decides which account signs the certificate. It does not decide which hostname a route may claim, so the second tenant's route still matches the first tenant's hostname on the shared gateway, and still gets a certificate for it. **The restriction has to be on the claim, not on the signature.**

### Why enforcement here and nowhere else

> Enforce at admission when a tenant's **action** takes something from another tenant at the moment it happens. Generate and check **standing configuration**, where the failure is an absence rather than an act.

A missing quota is a gap somebody should close, and a report is the right response. A hostname is gone by the time a report runs, and so is the rate limit.

### Why a ValidatingAdmissionPolicy

Core Kubernetes, generally available since 1.30. No policy engine, no operator, no webhook server, no certificate rotation for that webhook, and no additional component whose own outage decides whether the cluster accepts writes.

**Minimum Kubernetes version: 1.30.**

## Three details worth knowing before editing the file

**It fails closed.** `failurePolicy: Fail`. A policy that cannot be evaluated blocks route creation rather than permitting hostname theft.

**A route with no hostnames is rejected.** It is not a route claiming nothing — it inherits the gateway listener's hostname, so on a shared gateway with a wildcard listener it claims everything. The authorisation check itself is vacuously true over an empty list, so this needs its own rule.

**It matches every API version.** Listing versions explicitly is a bypass waiting to be discovered: an object submitted under a version the policy does not name is admitted unchecked.

## What it does not do

It does not stop a tenant with write access to another tenant's directory in this repository from editing that namespace's domain annotation. Repository permissions are the outer boundary; see `tenants/README.md`.

It does not restrict which Gateway a route attaches to. That is the listener's own `allowedRoutes.namespaces`, which names the namespaces whose routes may attach — verified against the v1.6.1 definition: *"AllowedRoutes defines the types of routes that MAY be attached to a Listener and the trusted namespaces where those Route resources MAY be present."*

**This is not `ReferenceGrant`, and conflating the two is easy.** A `ReferenceGrant` governs cross-namespace *references* — a route pointing a `backendRef` at a Service in another namespace, or a listener pointing a `certificateRef` at a Secret in another namespace. Its definition is explicit: it *"identifies kinds of resources in other namespaces that are trusted to reference the specified kinds of resources in the same namespace."*

The practical consequence of getting this backwards: you write a `ReferenceGrant`, believe attachment is governed, and leave `allowedRoutes` at a setting that admits more than you meant. Both mechanisms are needed, for different things.

---

**Not verified against a running cluster.** The CEL has not been evaluated by an API server.
