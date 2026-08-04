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
| Routing | **Gateway API** for HTTP | Platform owns the Gateway, tenants own Routes, and `allowedRoutes` decides who may attach |
| Observability | VictoriaMetrics stack, Vector → VictoriaLogs, Tempo, Grafana | Not exposed by default. Reached by port-forward |
| Databases | **none by default** | MariaDB, CloudNativePG and Valkey all ship commented out |
| Tenancy | Namespace per tenant, generated with its guarantees | Quota, limits, NetworkPolicy, PDB, backup, pod security, service account and RBAC |
| Backups | restic, one repository per namespace | The provider's block volumes have no snapshots at all |

### Defaults that cost money and are on anyway

Three-node HA control plane · etcd snapshots to object storage · a `Retain` StorageClass beside the deleting default · a backup CronJob generated with every tenant.

---

## Day zero

```bash
# 0. Check the instance types in your config are AVAILABLE, not merely supported.
#    Do this before every apply, not only on a schedule. On 2026-08-04 an entire
#    instance line went unavailable across all three datacentres of this region
#    within a day of being recorded as available, and the apply failed halfway.
python scripts/checks/check-pool-availability.py

# 1. Generate an object-storage credential in the provider console.
#    This step cannot be automated — the provider CLI has no object-storage commands.

# 2. Create the two buckets (state, versioned — snapshots, expiring)
./scripts/bootstrap-buckets.sh

# 3. Build the OS snapshot the nodes boot from. REQUIRED, and easy to miss:
#    the node module looks the image up by label and fails the plan if it is
#    absent. Nothing creates it for you.
packer init  hcloud-leapmicro-snapshots.pkr.hcl
packer build -only='hcloud.leapmicro-x86-snapshot' hcloud-leapmicro-snapshots.pkr.hcl

# 4. Build the cluster
tofu init && tofu apply

# 5. Generate the root of trust and install Flux.
#    Apply TWICE. The first apply creates the custom resource definitions and
#    then fails on the resources that use them -- kubectl does not wait for a
#    definition it created in the same pass to become established. The second
#    apply succeeds. This is expected, not a fault.
age-keygen -o age.agekey
kubectl apply -k clusters/<name>/flux-system   # creates CRDs, reports 2 errors
kubectl apply -k clusters/<name>/flux-system   # now succeeds
kubectl create secret generic sops-age \
  --namespace flux-system --from-file=age.agekey=age.agekey

# 6. Register a READ-ONLY deploy key on your fork and point the sync at it
```

Step 3 is not optional and is the most likely place a first run stops. The
module resolves its image by label selector, so a project without the snapshot
fails with `Resource (image) was not found using label selector` — which reads
like a permissions problem and is not one. The Packer template comes from the
upstream module; see `infrastructure/hetzner/README.md`.

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

**There are none, deliberately.** The tenant template generates the quota, limit range, NetworkPolicy, disruption budget, backup job, pod-security level, service account and RBAC. That *is* the tenancy model — a workload occupying a namespace demonstrates nothing the template does not already state.

`tenants/kustomization.yaml` ships with an empty resource list. Do not delete it because it looks pointless: without it the reconciler scans the directory, finds the template with its tokens unreplaced, and applies a namespace named after a placeholder — cleanly, and without error.

---

## Status — read this before trusting anything

Parts of this kit have now been **run on a real cluster**. Where a claim is verified it says so; where it is not, it says that too.

**Verified on a live cluster, 2026-08-04:**

- The node layer builds. `apply` completed with 45 resources and no errors; both nodes reached `Ready` on the pinned Kubernetes version.
- Flux installs from the committed manifests, and all four controllers run.
- **The multi-tenancy lockdown flags are live on the running controllers**, in the correct per-controller matrix — verified by reading container arguments off the cluster, not by inspecting patch files.
- **Flux reconciles from a git source and applies what it finds**, under those lockdown flags, using a named service account.
- **The hostname-authorisation admission policy compiles and enforces.** A route claiming another tenant's hostname is refused; so is a route declaring no hostname at all, which would otherwise inherit a shared listener's and claim everything it serves.
- `destroy` is clean. Repeated teardowns removed 26, 22 and 39 resources with zero residue.
- Object storage supports conditional writes, so native state locking is real, and default Go SDK checksums work, so no workaround ships.

**Not yet verified:**

- **cert-manager issuing a real certificate** — needs a domain pointed at the cluster.
- **The platform and observability layers reconciling from this repository**, which needs it published and a deploy key present. Their manifests build and dry-run cleanly against a live API server, but no controller has installed them.
- **The tenant template applied for real** — its manifests validate, but no tenant has been created from it.
- **The full HA shape.** A project-level address quota capped the test at two nodes, and the module correctly refuses a two-node control plane, so anti-affinity and disruption-budget behaviour remain untested by construction.

Prices and instance availability quoted anywhere here are dated observations, not permanent properties — see `LESSONS.md` for how fast that turned out to matter.

---

## Why the decisions are what they are

Every decision was worked as a ticket with its reasoning, alternatives and evidence recorded. Start at the [decision map](../../issues/1); each entry links to the ticket holding the detail.

The lessons those decisions produced live in [LESSONS.md](LESSONS.md) — stated as mechanisms with public citations, so they are verifiable by someone who has never seen the cluster that taught them.

---

## Licence

MIT. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
