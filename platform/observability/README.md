# Observability

Metrics, logs, traces and probes for the platform — and one decision that
matters more than all four.

**Grafana is not exposed.** No Ingress, no HTTPRoute, no hostname, no
LoadBalancer. Nothing in this directory creates a route to anything. The
documented way in is `kubectl port-forward`, and exposing it is a deliberate
act with its own checklist further down.

| Concern | Choice | Pinned |
|---|---|---|
| Metrics | VictoriaMetrics k8s stack — vmsingle, vmagent, vmalert, vmalertmanager, kube-state-metrics, node-exporter | chart `0.87.0` |
| Logs | VictoriaLogs, fed by Vector as a DaemonSet | chart `0.13.9` (vector `0.57.0` locked inside it) |
| Traces | Tempo, single binary, local storage | chart `2.2.3` |
| Probes | blackbox-exporter | chart `11.16.0` |
| Dashboard | Grafana, bundled with the metrics stack | chart `12.7.2` via the stack |

Every chart version is exact, and so is every URL the stack downloads at apply
time — see [Pinning](#pinning-including-the-part-that-is-not-a-chart), which is
less obvious than it sounds.

> **Status:** none of this has been run against a cluster. It renders — all
> four charts were rendered locally with these exact values on 2026-08-04, and
> the render contains zero Ingress, HTTPRoute or LoadBalancer objects — but
> rendering is not running. Treat every number here as a starting point that
> wants measuring.

---

## Why this stack rather than the usual one

The default answer for Kubernetes observability is Prometheus with Thanos or
Mimir for retention, Loki for logs, and the same Grafana on top. That
combination is excellent and this is not an argument that it is wrong. It is an
argument about which problems a small platform actually has.

**Resident footprint.** The metrics half of this layer is four processes:
scrape, store, evaluate, route. The common alternative reaches long retention
by adding an object-storage tier, a compactor, a store gateway, a query
frontend and a ring for them to agree with each other. Each is a component that
can be down at three in the morning while the thing it monitors is fine. On a
cluster of a handful of nodes, the monitoring stack should not be the most
complicated distributed system in it.

**Single-binary operation.** Storage here is one process writing to one disk.
That maps honestly onto the underlying storage — see below — instead of
pretending a single-writer volume is a distributed one. It also means the
failure modes are legible: full disk, dead process, corrupt index. Nothing
depends on quorum, and there is no ring whose state can be interesting.

**Retention costs disk, not architecture.** Thirty days of metrics is a number
in `retentionPeriod` and a volume size. It is not a second storage tier with
its own credentials, lifecycle rules and failure modes. The compression is good
enough that this stays true for considerably longer than thirty days.

**One query language across metrics and logs.** MetricsQL is PromQL plus
additions; LogsQL is close enough that moving between the two datasources is
not a context switch. The practical effect is that the person debugging at
three in the morning is not also learning a query language.

**The honest costs.** Fewer people know these components, so more of the
answers you find online are for the other stack. The single-node storage tier
scales vertically, so growth is a bigger machine and a bigger disk rather than
more replicas — see the sizing section, which says the same thing in numbers.
And Tempo is here from the other ecosystem entirely, because there was no
reason to be doctrinaire about traces.

---

## Reaching Grafana

```bash
kubectl -n observability port-forward svc/victoria-metrics-k8s-stack-grafana 3000:80
```

Then <http://localhost:3000>, and log in with the credential from
`secrets/grafana-admin.sops.yaml` — which you create yourself; there is no
default password and Grafana will not start until the secret exists. See
[`secrets/grafana-admin.sops.yaml.example`](secrets/grafana-admin.sops.yaml.example).

The other components, when you need them directly:

```bash
# Metrics store — MetricsQL, target status
kubectl -n observability port-forward svc/vmsingle-victoria-metrics-k8s-stack 8428:8428

# Evaluator — the ONLY authority on which alert rules are actually loaded
kubectl -n observability port-forward svc/vmalert-victoria-metrics-k8s-stack 8080:8080
curl -s localhost:8080/api/v1/rules | jq '[.data.groups[].name] | sort'

# Log store
kubectl -n observability port-forward svc/victoria-logs-single-server 9428:9428
```

A port-forward is not authentication. It narrows who can reach the port to
people who already hold cluster credentials, and does nothing else. That is why
Grafana's own authentication is hardened anyway — anonymous access explicitly
off, no default password, new users assigned Viewer — rather than left at
defaults behind the absence of a route.

> Two layers where the inner one is disabled is one layer wearing a costume.
> Defence in depth is a claim about what happens when the outer layer fails, so
> it only means anything if the inner layer is enabled *while* the outer one is
> working.

There is a second reason, more practical than philosophical: whoever adds a
route in eight months inherits whatever these defaults were. They will not
rediscover this reasoning first. They will discover a dashboard that already
works.

---

## What exposing it would cost

Stated concretely, because "secure your dashboards" is advice everyone agrees
with and nobody acts on.

**It is a query interface, not a set of charts.** The explore view runs
arbitrary queries against every configured datasource. Read access to the
dashboard is read access to everything the platform has ever recorded — not to
the panels somebody built, to the data underneath them.

**Logs carry credentials.** Not by design; by accident, constantly. Request
paths with tokens in the query string, stack traces containing connection
strings, a debug line somebody added during an incident and never removed.
Anyone who has grepped their own logs for a secret and found one already knows
this is not hypothetical. That is why there is no redaction transform in the
log pipeline: a regex denylist catches the pattern you thought of and gives you
permission to stop thinking about the rest. The access boundary is the control.

**Traces are the architecture diagram nobody wrote down.** Service names, call
graphs, database statements, timings, which internal service talks to which and
how often. It is a free reconnaissance pass, better organised than anything an
attacker would have assembled by hand.

**The datasources point inward.** Their definitions hold cluster-internal
addresses, which makes the dashboard a queryable window onto services that are
otherwise unreachable from outside the cluster.

**Editor is not a read-only role.** It carries dashboard write access and alert
contact points that can be repointed outward.

**It is cross-cutting by design, so one credential is every tenant at once.**
Everything else in this kit is built around a namespace being a boundary:
quota, policy, service account, backup repository. This layer deliberately
reads across all of them and has no per-tenant isolation inside it. It is the
one place where the tenancy model does not apply, which makes a single
credential here worth more than a credential anywhere else.

The safest configuration is the one that does not exist. Every alternative
considered in the decision that produced this layer — hardened built-in auth,
an ingress credential, a proxy against an external identity provider — is a way
of *defending* an internet-facing observability dashboard. None is as safe as
not having one.

### If you expose it anyway

Sometimes you have to. Then do all of these, not some:

1. `security.cookie_secure = true` in the Grafana values. It is currently
   `false`, and the comment at that line explains why: the documented access
   path is plain HTTP to localhost, and a secure-only cookie makes a
   port-forward login impossible. The moment there is TLS in front, that
   trade-off inverts.
2. A forward-auth middleware in front, delegating to an identity provider, with
   the built-in authentication still enabled underneath it. Not instead of it.
3. Confirm anonymous access is off in the *running* instance, not in the values
   file. A real cluster once ran anonymous access at Editor privilege behind a
   comment asserting the inner role was read-only. The comment was wrong by a
   privilege level and nothing disagreed with it for months.
4. Expose the dashboard only. vmsingle, vmalert, VictoriaLogs and Tempo have no
   authentication of their own at all; a route to any of them is the same
   exposure with the login page removed.

---

## Retention and storage sizing

| Store | Retention | Volume | Ceiling |
|---|---|---|---|
| VictoriaMetrics | 30d | 50Gi | read-only below 5GB free |
| VictoriaLogs | 14d | 50Gi | drops oldest above 40GB used |
| Tempo | 72h | 20Gi | **none — time-based retention only** |
| Grafana | — | none | dashboards are provisioned, not stored |

These are starting points, not measurements. Watch `vm_data_size_bytes` and the
volume stats for a fortnight and then set real numbers.

**Sizing and backup are not the same problem, and here they are not even
related.** The provider's block storage supports no snapshots whatsoever — the
CSI driver ships no snapshot class, so there is no snapshot-based backup path
to configure and none to add later. Consequently:

- **None of these volumes is backed up, and that is a decision, not a gap.**
  Metrics, logs and traces are accepted as unrecoverable if the volume is lost.
  If some of it must survive, replicate it outward as it arrives — vmagent's
  additional remote write — rather than planning to restore a disk.
- **The volumes are single-writer.** One node may mount each at a time, which
  is why every store here runs one replica. "Add a replica" is not a capacity
  answer; the scaling axis is vertical.
- **Growing a volume interrupts the workload holding it.** Running out of space
  is therefore a maintenance window, which is why two alerts warn about it —
  one at 85% full, one predicting four days ahead — and why the log store has a
  disk ceiling *below* its volume size. A store that drops old data is an
  inconvenience. A store that fills its disk is an outage on a schedule
  somebody else chose.

Tempo is the exception worth watching: it enforces retention by block age only,
with no disk-usage ceiling available. A traffic spike or a sampling-rate change
fills that volume. If you raise the sampling rate, revisit the number in the
same change.

---

## Alert rules

Rules come from two places, and the difference matters.

**Written here**, in `configs/`: the platform's own alerts — storage filling,
the log shipper missing from a node, probes failing, certificates expiring —
plus the tenant guardrails below.

**Downloaded at apply time**: everything else. The metrics chart does not
template its default rules. It runs a post-install Job that fetches them over
the public internet and creates rule objects from what it gets, and its own
defaults point at `main` and `master`.

> **THE RULES THAT END UP RUNNING ARE NOT IN THIS REPOSITORY.** You cannot
> review them by reading it, and a rule can be committed, applied, and still
> not be loaded — the Job can fail, a source can 404 after an upstream
> reorganises, a group can be filtered out.
>
> **Verify against the running evaluator, never against the repository.**
>
> ```bash
> kubectl -n observability port-forward svc/vmalert-victoria-metrics-k8s-stack 8080:8080
> curl -s localhost:8080/api/v1/rules | jq '[.data.groups[].name] | sort'
> ```

`configs/rules-loaded.yaml` is the automated half: a recording rule that always
produces a value, and an alert on its absence. It proves that group is loaded
and says nothing about any other — a canary, not a checksum.

### The tenant guardrails report; they do not fail

`configs/rules-tenant-guarantees.yaml` names any namespace labelled as a tenant
that is missing a quota, limit range, network policy, disruption budget or
backup job.

They are `severity: info` on purpose:

> Security boundaries fail. Standing configuration reports.

An absent quota is a gap for somebody to close. An absent privilege boundary is
a hole to stop. Conflating them trains people to ignore both. These are
generated by the tenant template, so their absence means a namespace predates
the template learning to generate them — generation is prospective and never
goes back, which is the entire reason a check exists alongside a generator.

**Expect the first run on a mature cluster to find something.** A check that
finds nothing is more likely broken than clean. `TenantGuardrailMatchesNothing`
exists for exactly that: if the tenant label, the kube-state-metrics allowlist
and these expressions ever stop agreeing, every other check in the file
evaluates over an empty set and reports perfect compliance forever.

### Alerts currently go nowhere

The alertmanager configuration routes everything to a blackhole receiver, and
must, because a receiver is a destination and a credential and neither belongs
in a public repository.

**An alerting stack whose receiver is a blackhole is indistinguishable from no
alerting stack, and it looks healthy while being useless.** Replace it with a
real receiver — via a SOPS-encrypted secret and `spec.configSecret` — before
relying on anything in this layer.

---

## Pinning, including the part that is not a chart

Every chart reference is an exact version. So is every URL the sync Job
fetches: release tags where upstream publishes them, commit SHAs where it does
not. All 37 resolved on 2026-08-04, including the ten belonging to components
this kit does not deploy — a disabled floating reference is a floating
reference the day somebody enables it.

This second half is easy to miss, and it is the more dangerous one. A floating
chart version is at least resolved once and recorded in a release; a floating
URL is re-resolved on every reconcile, from a branch anybody upstream can push
to, and the result is applied to your cluster without appearing in any diff.

Bump them deliberately, together with the chart, and read what changed.

---

## Layout

```
controllers/    namespace, chart sources, HelmReleases — installs the CRDs
configs/        scrapes, probes, alert rules — consumes those CRDs
secrets/        the one hand-made secret, not applied by either kustomization
```

The split is not cosmetic. Everything in `controllers/` installs custom
resource definitions and everything in `configs/` requires them, so the cluster
reconciliation for `configs/` must declare a dependency on the one for
`controllers/`. Without that edge the first apply fails, retries, and
eventually succeeds — which reads as flakiness rather than as a missing
dependency, and is how ordering bugs survive for years.

Within the layer the same ordering is declared where the mechanism allows it:
the VictoriaLogs release declares `dependsOn` the metrics stack, because it
creates a scrape object defined by the operator that stack installs.
