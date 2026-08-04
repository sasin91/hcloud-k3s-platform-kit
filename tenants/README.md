# Tenancy

A tenant is a namespace that arrives with guarantees attached. Without them, "a namespace per tenant" is a naming convention — and a naming convention does not stop one tenant reading another tenant's database over the pod network, consuming the cluster, or claiming its hostname.

**The kit ships no example tenant.** `_template/` is the demonstration. A workload occupying a namespace shows nothing the template does not already state, and every workload shipped is one more thing to keep working against a cluster nobody has verified.

---

## The contract

Every tenant gets all of this, and cannot opt out of any of it:

| Guarantee | What it stops |
|---|---|
| **Namespace**, carrying the managed label and a pod security level | automation touching things it does not own; a privileged pod in a tenant namespace |
| **ResourceQuota** — requests, limits, storage, object counts | one tenant consuming the cluster, or filling etcd from inside a namespace |
| **LimitRange** — per-container defaults | containers running unbounded because nobody wrote a request |
| **NetworkPolicy** — default-deny ingress and egress, with stated allowances | every tenant reaching every other tenant's database |
| **PodDisruptionBudget** | a node drain taking a tenant out all at once |
| **Backup CronJob** — restic, one repository per namespace | a tenant existing with no way back |
| **ServiceAccount, Role, RoleBinding**, named by the tenant's reconciliation | the tenant's manifests being applied as cluster administrator |
| **Storage class assignment** | stateful data landing on a class whose reclaim policy is Delete |

The mechanics of each are commented at the line they constrain, in `_template/`. The reasoning is not repeated here, because prose that restates configuration is prose that drifts from it.

---

## Generated, enforced, reported

Three different things, and conflating them is how people learn to ignore all three.

**Generated.** Everything in the table above. The template that creates the namespace creates them with it. This is not a stylistic preference — it was measured. On a mature multi-tenant cluster of thirteen tenants:

```
ResourceQuota        0/13
LimitRange           0/13
PodDisruptionBudget  0/13
NetworkPolicy        0/13
backup CronJob      12/13
```

The one guarantee that exists almost everywhere is the one the provisioning path generated. The four that exist nowhere are the four that were documented as recommendations. The axis is not enforcement versus suggestion. It is **in the generator versus not in the generator**.

**Enforced.** One thing: which hostnames a tenant's routes may claim, rejected at admission by a `ValidatingAdmissionPolicy` in `platform/configs/admission/`.

The line falls there and not elsewhere, and the rule generalises:

> Enforce at admission when a tenant's **action** takes something from another tenant at the moment it happens — claiming a hostname, obtaining a certificate for a name it does not own. Generate and check **standing configuration**, where the failure is an absence rather than an act.

An absent quota is a gap for somebody to close. A stolen hostname is already gone by the time a report runs.

**Reported.** A scheduled in-cluster check names every namespace carrying the managed label that lacks a quota, limit range, network policy, disruption budget or backup job. It reports; it does not fail and it does not remediate.

**Expect the first run on a mature cluster to find something.** Generation is prospective: it covers tenants created after it learned to, and never revisits. On the cluster measured above, the single missing backup was the oldest tenant, provisioned before the backup automation existed — and no improvement to the template would ever have found it. A check that finds nothing on a mature cluster is more likely broken than clean.

---

## The label contract, in both directions

```yaml
platform.example.com/managed: tenant          # automation acts here
platform.example.com/tenant: <name>           # and this is which tenant it is
platform.example.com/domains: <annotation>    # and these are the hostnames it may claim
```

Read forwards, the label is how tooling finds tenants. Read backwards, it is the anti-tenant guard: **a namespace that does not carry it is out of scope by construction.** `kube-system`, `flux-system` and the platform's own namespaces are protected by never carrying the label, rather than by an exception list somebody has to remember to extend when they add a namespace.

The label is platform-owned. A tenant's Role grants no verb on namespaces, so a tenant cannot relabel itself out of the admission binding, or annotate another tenant's domain onto itself.

---

## Limits, stated rather than discovered

**Egress restriction is CIDR-only.** Standard NetworkPolicy has no concept of a hostname, so "this tenant may reach `api.example.com` and nothing else" is not expressible. It cannot be approximated either — the addresses behind an interesting hostname belong to a CDN and change without notice. What ships is the coarse form: not the cluster, not the node network, not the metadata endpoint, one port. A CNI with FQDN policy is the documented opt-in, and it is a decision made when the cluster is built, because the CNI is a create-time choice on this node module and is not migrated to later.

**The backup does not cover a volume held by a running pod.** The provider's volumes are single-writer and a plain CronJob does not co-schedule itself onto the node holding one. The database-dump path avoids volumes entirely; a node-pinning backup operator is the documented upgrade path for the rest. This is named rather than pretended away, because "backups are configured" is routinely read as "tenants are recoverable".

**Repository write access is the outer boundary.** The in-cluster boundary holds against anything applied through a tenant's reconciliation. It does not hold against editing the file that decides which identity performs it. Code ownership on `tenants/<name>/` is what enforces that, and Kubernetes cannot tell you who is allowed to commit.

**Template improvements do not reach existing tenants.** Tenants are copies. See `_template/README.md` for why that trade was taken and what it costs.

---

## Adding a tenant

`_template/README.md`. Three tokens, two hand-made secrets, and one line added to `kustomization.yaml` in this directory.

That last line is not bookkeeping. The cluster's tenants stage applies `./tenants`, and the explicit resource list is what declares which directories are tenants — which is also what keeps `_template/` from being applied as one. Removing the line offboards the tenant: the stage prunes the namespace and everything in it.

---

**Verified on a running cluster, 2026-08-04.** A tenant was generated from
`_template` by following the procedure above verbatim, and serves real traffic:
its workload is reconciled by its own service account under `restricted` pod
security, its route attaches to the platform Gateway through `allowedRoutes`,
and the page is fetched over the public internet with a Let's Encrypt
certificate the system trust store validates.

Doing that found three defects that made "tenants own Routes" false in practice
while being true in this file — workloads applied at cluster scope, no listener
a tenant was permitted to attach to, and a NetworkPolicy selecting a label
nothing applied. All three are fixed; see LESSONS.md for why each was invisible.

What is still unverified here: the backup CronJob has never restored anything,
and no tenant has been offboarded by removing its line from
`kustomization.yaml`.
