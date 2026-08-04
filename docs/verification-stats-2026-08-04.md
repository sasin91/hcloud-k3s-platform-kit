# Verification snapshot — 2026-08-04

Numbers measured on the live verification cluster before teardown. Everything
here is an **observation with a date**, not a property of the kit. Prices,
instance availability and series counts all moved during the two days this took.

**Every figure below is from the verification cluster** — three nodes in `fsn1`,
running the kit and one tenant serving a static site. See the *Measurement
hazard* section at the bottom: an earlier draft of this file had numbers from a
different cluster entirely.

---

## The cluster that produced these numbers

| | |
|---|---|
| Kubernetes | `v1.35.6+k3s1` |
| Nodes | 1 control plane (`cpx22`), 2 agents (`cpx32`) |
| Allocatable | 9,400m CPU · 17.7 GiB memory |
| Region | `fsn1` |
| Tenant workload | one static site, 2 replicas, nginx-unprivileged |

The kit's **default** shape is 3 control planes + 2 agents. This ran 1 + 2
because the verification project's server quota was 4. See `LESSONS.md`.

## Cost

| Item | Type | EUR/month gross |
|---|---|---|
| Control plane × 1 | `cpx22` (2c/4G) | 24.36 |
| Agents × 2 | `cpx32` (4c/8G) | 88.72 |
| Load balancer | `lb11` | 9.36 |
| **Total** | | **122.45** (97.96 net) |

Plus 120 GB of block storage created by the CSI driver for the observability
stack — three PersistentVolumeClaims that `tofu destroy` does not remove,
because it never created them.

Gross figures include 25% Danish VAT. The full default shape (3 control planes,
2 agents) would be **171.17 gross**, before the autoscaler adds anything.

## What the platform costs you before you run anything

Measured with `kubectl top`, cluster at rest, one tenant serving a static site.

| Namespace | Pods | CPU | Memory |
|---|---|---|---|
| `observability` | 19 | 141m | 2,858 Mi |
| `flux-system` | 4 | 9m | 596 Mi |
| `kube-system` | 11 | 33m | 580 Mi |
| `cert-manager` | 6 | 6m | 286 Mi |
| `traefik` | 2 | 4m | 150 Mi |
| `system-upgrade` | 1 | 1m | 56 Mi |
| **platform total** | 43 | **194m** | **4,526 Mi** |
| `tenant-sasin91` | 2 | 2m | **8 Mi** |

The two numbers worth a paragraph in a blog post:

- **Observability is 63% of the platform's memory.** Metrics, logs and traces
  cost more than everything that delivers, routes, secures and upgrades the
  cluster combined.
- **The application is 8 MiB.** The platform is 566× the workload it exists to
  run. That ratio is the honest cost of "production-shaped", and it does not
  improve until you have many tenants.

CPU is a rounding error: 194m of 9,400m allocatable, about **2%**. Memory is the
constraint on this shape, not CPU — 68% used on two of three nodes.

## Object counts

| | |
|---|---|
| Namespaces | 10 |
| Pods | 47 |
| Deployments / StatefulSets / DaemonSets | 24 / 3 / 4 |
| Services | 27 |
| ConfigMaps / Secrets | 75 / 33 |
| NetworkPolicies | 10 |
| CustomResourceDefinitions | 81 (10 of them Gateway API) |
| Flux Kustomizations / HelmReleases / sources | 10 / 8 / 10 |

**81 CRDs** is worth noting on its own: a "minimal" k3s platform installs eighty
one API types before a tenant exists.

## Telemetry volume

A three-node cluster, idle apart from one static site.

| | |
|---|---|
| Active time series | **161,507** |
| Label/value pairs | ~3.0 million |
| Log lines per hour | **7,613** |
| Distinct trace services | 1 (`traefik`) |

Top metric families by series count:

| Metric | Series |
|---|---|
| `apiserver_request_duration_seconds_bucket` | 24,720 |
| `etcd_request_duration_seconds_bucket` | 19,200 |

Two histograms from the control plane are **27% of all series on the cluster**.
Nothing on this cluster queries them. This is the strongest argument in the kit
for dropping or aggregating default histogram buckets before scaling out — the
cost is per-series and it lands whether or not anyone looks.

Log lines by namespace, last hour: `kube-system` 4,904 · `tenant-sasin91` 1,066
· `observability` 564 · `flux-system` 535 · `traefik` 365 · `system-upgrade` 124
· `cert-manager` 55.

The tenant serving a handful of requests produced 1,066 log lines an hour —
second only to the Kubernetes control plane.

## Grafana, as provisioned

| | |
|---|---|
| Datasources | 5 — VictoriaMetrics (two flavours), VictoriaLogs, Tempo, Alertmanager |
| Provisioned dashboards | **42** |
| Unauthenticated `/api/datasources` | **401** |

All four signals are wired to a datasource without anyone configuring one, and
42 dashboards exist before you build any. That is the argument for the bundled
stack and also the argument against it: nobody reads 42 dashboards, and every
one queries series you are paying to retain.

The 401 matters more than it looks. Anonymous access being off is asserted in
configuration and checked in CI; this is the runtime confirmation that the
dashboard sitting in front of every log line and trace on the cluster refuses
an unauthenticated API call.

## Build and teardown timings

| Operation | Result |
|---|---|
| `tofu apply` (clean) | **55 resources added** |
| `tofu destroy` (clean) | **55 resources destroyed**, exit 0 |

Final teardown, following the documented order:

1. `scripts/teardown.sh --delete` removed 3 CSI volumes and 1 cloud-controller
   load balancer — **none of which OpenTofu knew about**.
2. `tofu destroy` then completed cleanly, 55 resources, exit 0.
3. Re-listing the whole project found **0 servers, 0 load balancers, 0 volumes,
   0 networks, 0 firewalls, 0 primary IPs, 0 floating IPs**.

What remained: one Packer-built OS snapshot (1.42 GB actual, ~EUR 0.02/month,
worth keeping — rebuilding it is a Packer run), plus a placement group and two
SSH keys that all pre-dated the verification and belong to the account, not to
this cluster.

Had step 1 been skipped, step 2 would have failed on the network the load
balancer was still attached to.

Four full build/teardown cycles were run over the verification. Node layer to
`Ready` is minutes; **full reconciliation of the whole chain is the long pole**,
because each layer's health gate must pass before the next applies.

## The repository itself

| | |
|---|---|
| Tracked files | 122 |
| YAML files | 74 |
| Total tracked lines | 27,182 |
| Comment lines in YAML | 3,819 |
| Non-comment YAML lines | 14,106 |
| **Comment density in YAML** | **27%** |
| Guardrail checks | 14 |
| Lessons in `LESSONS.md` | **49** |
| `LESSONS.md` | 539 lines |

More than a quarter of the YAML is comments, and that is deliberate: nearly
every one records why a value is what it is, and several record what happened
when it was something else.

## Verified end to end

- Three-node HA control plane forms with etcd (verified on an earlier cycle at
  the kit's default shape).
- 9/9 Kustomizations and 8/8 HelmReleases reconcile from git.
- SOPS-encrypted secret committed to git, decrypted in-cluster, consumed by the
  component that needs it.
- A tenant generated from `_template` serves traffic over the public internet
  with a **Let's Encrypt production certificate the system trust store
  validates** — `sasin91.quazye.lol`, TLSv1.3, no `-k`.
- Traces from a laptop request arrive in Tempo carrying `k8s.pod.name`,
  `k8s.namespace.name`, `k8s.node.name`, `k8s.deployment.name`.
- Hostname admission policy refuses another tenant's hostname, and refuses a
  route declaring none.

---

## Measurement hazard, recorded because it nearly ended up in a blog post

The first version of this file reported **477,140 active series** and **131,325
log lines per hour**. Both were wrong by roughly 3× and 17×, and neither came
from this cluster.

`kubectl port-forward` was told to bind `8428` and `9428` — the default ports
for these two components. Tunnels to a *different, production* cluster were
already bound there. The port-forward failed to bind, `curl` connected to the
tunnel that already owned the port, and every query returned real, plausible,
internally consistent data about somebody else's cluster.

It was caught only because a namespace list came back naming namespaces that do
not exist here.

The detail that made it silent: the port-forward was launched with `>/dev/null
2>&1`, and `bind: address already in use` goes to **stderr**. The one signal
that would have caught it was explicitly discarded.

> Suppressing a command's error output does not make it succeed. For anything
> that binds a port or opens a tunnel, bind somewhere unusual, and check *what
> answered* rather than that something did.
