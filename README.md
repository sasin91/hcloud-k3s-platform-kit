# hcloud-k3s-platform-kit

A decided k3s platform for Hetzner Cloud — GitOps delivery, encrypted secrets, observability, multi-tenancy, and the operational guardrails that come from having got these things wrong before.

**This is not another cluster installer.** Creating a k3s cluster on Hetzner is a solved problem. This is what you install afterwards, and it is mostly a set of decisions with the reasoning attached.

---

## Built on

This kit consumes [**terraform-hcloud-kube-hetzner**](https://github.com/mysticaltech/terraform-hcloud-kube-hetzner) (MIT) as a pinned module dependency. That project solves cluster creation — image building, cloud-init, HA control planes, cloud controller and CSI wiring, coordinated node upgrades — and none of it is reimplemented here.

It is a dependency, not a fork. See [NOTICE](NOTICE).

---

## What's in it

| Layer | Choice | Why |
|---|---|---|
| Infrastructure | **OpenTofu**, module pinned to a major version | State *and plan files* encrypt client-side; Terraform has no equivalent |
| State | S3-compatible object storage, versioned, native locking | Verified working, not assumed — see below |
| Delivery | **Flux**, controller manifests committed | Pruning, drift correction, health assertions. No token, no write-back to your repo |
| Secrets | **SOPS + age** | Exactly one secret is applied by hand and cannot be encrypted. Everything else lives encrypted in git |
| Networking | **Flannel** | Already enforces NetworkPolicy. Cilium documented as opt-in for FQDN egress |
| Certificates | **cert-manager** only | The ingress runs no ACME client, so it runs more than one replica |
| Routing | **Gateway API** for HTTP | Its platform/tenant/ReferenceGrant split *is* the tenancy model |
| Observability | VictoriaMetrics stack, Vector → VictoriaLogs, Tempo, Grafana | Not exposed by default. Reached by port-forward |
| Databases | MariaDB operator | CloudNativePG and Valkey ship commented out |
| Tenancy | Namespace per tenant, generated with its guarantees | Quota, limits, NetworkPolicy, PDB, backup, pod security, service account and RBAC |
| Backups | restic, one repository per namespace | The provider's block volumes have no snapshots at all |

### Defaults that cost money and are on anyway

Three-node HA control plane · etcd snapshots to object storage · a `Retain` StorageClass beside the deleting default · a backup CronJob generated with every tenant.

---

## Day zero

```bash
# 1. Generate an object-storage credential in the provider console.
#    This step cannot be automated — the provider CLI has no object-storage commands.

# 2. Create the two buckets (state, versioned — snapshots, expiring)
./scripts/bootstrap-buckets.sh

# 3. Build the cluster
tofu init && tofu apply

# 4. Generate the root of trust and install Flux
age-keygen -o age.agekey
kubectl apply -k clusters/<name>/flux-system
kubectl create secret generic sops-age \
  --namespace flux-system --from-file=age.agekey=age.agekey

# 5. Register a READ-ONLY deploy key on your fork and point the sync at it
```

No personal access token. No admin-scoped credential. Nothing written back into your repository on your behalf. You can read exactly what will be applied before applying it, and the Flux version is a line in your own diff.

**Exactly one secret in the whole system is created by hand and cannot be encrypted** — the age key. Store it in a password manager. For more than one operator, encrypt to multiple recipients rather than sharing a private key.

---

## Guardrails

Checks are split by *where the truth lives*, not by severity.

**In CI, failing the pull request** — every module and chart reference carries an explicit version · no duplicate YAML keys · controller manifests not stale against upstream · ingress declares more than one replica and a disruption budget · no ACME resolver alongside cert-manager · dashboard anonymous access off · every controller carries the multi-tenancy lockdown flags · every tenant reconciliation names a service account · pruning not disabled without a recorded reason.

**Against the provider API, on a schedule** — every declared nodepool names an instance type *currently available* in its location. Availability is not static, and a pool created when its type was in stock keeps that type forever.

**Inside the cluster, surfaced as alerts** — tenants missing a guarantee · alert rules verified against the running evaluator rather than the repo · pools carrying taints nothing tolerates.

No kubeconfig is ever placed in a CI system.

> **Security boundaries fail. Standing configuration reports.** An absent quota is a gap for somebody to close; an absent privilege boundary is a hole to stop. Conflating them trains people to ignore both.

---

## Deliberately not included

- **A private registry.** It is a hostname, a certificate, credentials, a storage decision and a garbage-collection policy. Most adopters on day one run images they did not build. Available as an add-on.
- **A public route to the dashboard.** Every alternative is a way of *defending* an internet-facing observability dashboard. None is as safe as not having one.
- **An egress nodepool.** A floating address does nothing without a CNI that implements egress gateway or a workload pinned to the node holding it. Shipping the pool without the mechanism is dead cost.
- **A mail stack**, in any form.
- **Arm nodepools.** x86 is cheaper at identical specs on this provider, and at the time of writing no Arm instance type was available in any datacentre in the region. Platform components stay multi-architecture because that costs nothing.

---

## Example tenants

Two game-server emulators, as **two different workloads sharing one cluster** — which demonstrates tenancy better than two copies of one thing. They exercise paths a web application never touches: raw TCP with no hostname multiplexing, a database per tenant, and a simulation that does not scale horizontally at all.

Realm traffic **bypasses the ingress proxy entirely**, on a load balancer with an explicitly declared port pair per realm.

> A reverse proxy is a shared restart domain. It terminates every connection passing through it, so restarting it destroys them all. For HTTP that is invisible. For a long-lived session it means every user disconnected, every time anyone touches ingress.

**For educational purposes.** This repository ships no game data and no means of obtaining any. See [NOTICE](NOTICE).

---

## Status — read this before trusting anything

**Almost nothing here has been verified against a running cluster.** The first real pilot is the verification. Where a claim has been measured, it says so and carries a date.

Measured on 2026-08-03, one location, one project: the object storage supports conditional writes, so native state locking is real and ships enabled; and the Go SDK's default checksum behaviour works, so **no checksum workaround ships** — adding one defensively would disable an integrity check that demonstrably works.

Prices and instance availability quoted anywhere in this repository are dated observations, not permanent properties. Both move.

---

## Why the decisions are what they are

Every decision was worked as a ticket with its reasoning, alternatives and evidence recorded. Start at the [decision map](../../issues/1); each entry links to the ticket holding the detail.

The lessons those decisions produced live in [LESSONS.md](LESSONS.md) — stated as mechanisms with public citations, so they are verifiable by someone who has never seen the cluster that taught them.

---

## Licence

MIT. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
