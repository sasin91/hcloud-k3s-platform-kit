# ADR 0001 — VictoriaMetrics stack over Prometheus/Thanos/Loki

- **Status:** Accepted
- **Date:** 2026-08-04
- **Deciders:** platform maintainer (single operator)
- **Supersedes:** none
- **Verification:** unverified against a running cluster. Every quantitative figure below is vendor-published or documentation-published; none has been reproduced here. See [Verification status](#verification-status).

---

## Context

The kit needs metrics, logs and traces on a small self-hosted k3s cluster with the following constraints, all of which push in the same direction:

1. **One operator.** There is no observability team. Whatever is chosen has to be understood, upgraded and debugged at 02:00 by the same person who is also debugging the thing that woke them.
2. **RAM is the scarce resource and it is bought in steps.** On this provider you do not add 2 GiB — you move up an instance size for every node. Memory consumed by the platform is memory tenants cannot have, and the platform's observability stack is the single largest platform consumer on a cluster this size.
3. **The stack is not exposed.** It is reached by port-forward, by decision (see the README). So "great hosted UI" and "SSO integration" are not deciding factors; operational cost is.
4. **Retention target is months, not years.** Long enough to see a seasonal pattern and to investigate an incident from last quarter. Not long enough to need a cheap cold tier.
5. **Object storage exists but is not free.** Buckets are already provisioned for OpenTofu state and etcd snapshots. Adding a third bucket is cheap; putting object storage **on the query path** is a different commitment — it is a new failure mode for "can I look at a graph during an incident".
6. **The Prometheus data model is non-negotiable.** Everything that emits metrics in this ecosystem emits Prometheus metrics. Whatever is chosen has to scrape them and has to serve PromQL to Grafana.
7. **High-cardinality correlation is a first-class use case.** The question that actually gets asked during an incident is "show me everything for this request id / this trace / this tenant", not "show me a rate by job".

The obvious default is `kube-prometheus-stack`, extended with Thanos for retention and Loki for logs. That is what most of the ecosystem assumes, and choosing against it needs a reason.

---

## Decision

**Use the VictoriaMetrics stack for metrics and logs.**

| Concern | Component |
|---|---|
| Metrics storage + query | VictoriaMetrics single-node (`vmsingle`) |
| Scraping | `vmagent` |
| Rule evaluation | `vmalert` |
| Alert routing | Alertmanager (unchanged, upstream Prometheus project) |
| Logs collection | Vector |
| Logs storage + query | VictoriaLogs |
| Traces | Tempo |
| UI | Grafana, port-forward only |

Deployed through the VictoriaMetrics operator, so that `ServiceMonitor`, `PodMonitor`, `PrometheusRule`, `Probe` and `ScrapeConfig` objects shipped by third-party charts continue to work.

**Traces are the odd one out and are called out honestly:** Tempo is from the Grafana stack, kept because it is the mature option and because traces are lower-volume here than metrics or logs. VictoriaTraces was **not evaluated** for this decision. If it is evaluated later and is credible, the stack becomes single-vendor for all three signals, which is both an argument for it and an argument against it.

---

## Rationale, grounded in properties

### Single binary versus multi-component

VictoriaMetrics single-node *"consists of a single small executable without external dependencies"* and *"all the configuration is done via explicit command-line flags"* ([VictoriaMetrics single-server docs](https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/)). VictoriaLogs is described as *"a single zero-config executable"* ([VictoriaLogs docs](https://docs.victoriametrics.com/victorialogs/)).

Thanos, in the configuration that provides long-term retention, is at minimum Sidecar, Store Gateway, Querier and Compactor, plus object storage, with Receive and Ruler optional ([Thanos, Getting Started](https://thanos.io/tip/thanos/getting-started.md/)).

The Compactor deserves specific mention because it has an operational hazard with a manual-recovery failure mode:

> *"Because not all object storage providers implement a safe locking mechanism, you need to ensure on your own that only a single Compactor is running against a single stream of blocks on a single bucket."*
> — [Thanos, Compactor](https://thanos.io/tip/components/compact.md/)

Running two by accident risks *"dangerous data overlaps requiring manual resolution"*. Horizontal scaling is possible via label sharding, but the default posture is a singleton you must not duplicate. For a single-operator cluster this is a component with a sharp edge and no upside at our data volume.

**This is the primary reason for the decision.** Not benchmark numbers — component count and the number of distinct failure modes one person has to hold in their head.

### Loki, treated fairly

Loki is not automatically "the complicated option". It has a monolithic mode: *"This mode runs all of Loki's microservice components inside a single process as a single binary or Docker image"*, suitable for *"small read/write volumes of up to approximately 20GB per day"* ([Loki, Deployment modes](https://grafana.com/docs/loki/latest/get-started/deployment-modes/)). That is comfortably above what this cluster produces. Component count is therefore **not** a good argument against Loki at this size, and it would be dishonest to make it.

The real argument is the data model. Loki's documentation is explicit:

> *"High cardinality causes Loki to build a huge index and to flush thousands of tiny chunks to the object store."*
> *"High cardinality can lead to significant performance degradation. Prefer fewer labels, which have bounded values."* — recommending *"10 - 15 labels at a maximum"*, and directing high-cardinality search to structured metadata instead.
> — [Loki, Labels](https://grafana.com/docs/loki/latest/get-started/labels/)

VictoriaLogs claims the opposite design point: support for *"log fields with high cardinality (e.g. high number of unique values) such as `trace_id`, `user_id` and `ip`"* ([VictoriaLogs docs](https://docs.victoriametrics.com/victorialogs/)).

Constraint 7 above *is* the high-cardinality case. Loki has a good answer (structured metadata), but it is an answer you have to design for in advance, per field, at ingestion time. VictoriaLogs asks nothing of you at ingestion. For a platform that will ingest logs from tenant workloads whose shape it does not control, "no ingestion-time design decision required" is worth a great deal.

### Resource footprint — flagged as vendor claims

VictoriaMetrics publishes: *"up to 7x less RAM than Prometheus, Thanos or Cortex when dealing with millions of unique time series"* and *"up to 7x less storage space… compared to Prometheus, Thanos or Cortex"* ([single-server docs](https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/)). VictoriaLogs publishes *"up to 30x less RAM and up to 15x less disk space than other solutions"* including Elasticsearch and Loki ([VictoriaLogs docs](https://docs.victoriametrics.com/victorialogs/)).

**These are the vendor's own benchmarks, on the vendor's own workloads, and we have not reproduced any of them.** They are recorded here because they are the published claim, not because they are evidence. Anyone using this ADR to justify a capacity plan should measure.

What this decision *does* rely on is the structural claim, which needs no benchmark to check: one process per signal, and no object-storage round trip on the query path. You verify that by reading the Deployment, not by trusting a graph.

### Retention cost — the argument against this decision

This is the strongest reason to choose Thanos instead, and it belongs in the record in plain words.

With `vmsingle`, retention is **local disk on a block volume you pay for by the gigabyte-month**. There is no cheap tier. Thanos and Loki both push aged data to object storage, which is roughly an order of magnitude cheaper per gigabyte, and Thanos additionally downsamples: the Compactor creates *"5m downsampling for blocks older than 40 hours"* and *"1h downsampling for blocks older than 10 days"*, enabled by default ([Thanos, Compactor](https://thanos.io/tip/components/compact.md/)).

**VictoriaMetrics downsampling is Enterprise-only.** So are multiple retention periods, anomaly detection, `vmbackupmanager` automation, vmgateway rate limiting and mTLS between components ([VictoriaMetrics Enterprise](https://docs.victoriametrics.com/victoriametrics/enterprise/)).

So: the feature that makes multi-year retention affordable is free in Thanos and paid in VictoriaMetrics. **At long retention, Thanos is the cheaper and better answer, and this ADR is wrong.** It is only right in the regime described by constraint 4 — months of retention, where a modest block volume costs less than a bucket plus four components plus the query-path dependency.

That regime boundary is the single most important thing in this document, because it is the thing that will change.

### Query language

MetricsQL is documented as *"backwards-compatible with PromQL, so Grafana dashboards backed by Prometheus datasource should work the same after switching from Prometheus to VictoriaMetrics"* — while stating there are *"some intentional differences between these two languages"* ([MetricsQL](https://docs.victoriametrics.com/victoriametrics/metricsql/)). The documented differences include:

- `increase()` and `rate()` account for the last raw sample **before** the lookbehind window, where Prometheus misses the increase across that boundary;
- MetricsQL does **not extrapolate** `rate()` and `increase()`, returning integer-shaped results where Prometheus returns fractional ones;
- `rate()` returns non-empty results when the step is smaller than the scrape interval;
- scalars are treated as instant vectors without labels;
- `NaN` values are removed from output rather than returned;
- metric names are kept after functions that do not change the meaning of the series.

Read the second and third items carefully. **These differences produce different numbers, not errors.** A dashboard imported from Prometheus renders; an alert rule copied from a Prometheus-based runbook evaluates. They just do not necessarily produce the same value they did before — and no one is notified.

That is the exact failure shape [LESSONS.md](../../LESSONS.md) is built around: nothing crashed, everything was green. **Consequence for operators: alert thresholds carried over from a Prometheus deployment must be re-derived against real data, not trusted.** Treat "the dashboard still works" as evidence of syntax compatibility only.

For logs, LogsQL is not LogQL. Loki queries do not port at all. There is nothing silent about this — queries simply fail — but it means every community log dashboard is written for the other system, and whoever operates this learns a language they cannot reuse elsewhere.

### Prometheus-ecosystem compatibility, and its two traps

The VictoriaMetrics operator *"converts all existing Prometheus `ServiceMonitor`, `PodMonitor`, `PrometheusRule`, `Probe` and `ScrapeConfig` objects into corresponding VictoriaMetrics Operator objects"* by default ([VM operator, Prometheus integration](https://docs.victoriametrics.com/operator/integrations/prometheus/)). This is what makes third-party charts work unchanged, and it is a large part of why the decision is affordable at all.

Two documented behaviours will bite, and are recorded here so nobody rediscovers them during an incident:

1. **Converted objects are not deleted when the original is.** Owner references are opt-in via `VM_ENABLEDPROMETHEUSCONVERTEROWNERREFERENCES=true`. Without it, deleting a `ServiceMonitor` leaves a `VMServiceScrape` scraping happily — an orphan that survives the deletion of the thing it came from. Compare [LESSONS.md](../../LESSONS.md): *"the ones that survive deletion are disproportionately the ones somebody deliberately tried to remove."*
2. **Updates to the Prometheus object overwrite manual edits to the converted object**, unless annotated `operator.victoriametrics.com/ignore-prometheus-updates: enabled`.

Both are the same underlying hazard: **two objects claim authorship of one rule, and the loser is silent.** The mitigation is not documentation; it is the guardrail this kit already specifies — *verify alert rules against the running evaluator, not against the repository.* A rule that is present in git and absent from the evaluator is exactly what this class of bug looks like.

Conversion should also be turned **off** for resources the kit does not use (`-controller.disableReconcileFor`), so there is one authoring path rather than two.

### Ecosystem cost, stated without softening

- `kube-prometheus-stack` is the default in most Helm charts, most tutorials, most answers you will find when searching an error, and most vendors' "how to monitor us" pages. Choosing VictoriaMetrics means **fewer people have debugged what you are running.** When something is weird, you are reading source and GitHub issues rather than the fourth Stack Overflow result.
- VictoriaLogs is used in Grafana through a **Grafana plugin** rather than a built-in datasource ([VictoriaLogs docs](https://docs.victoriametrics.com/victorialogs/)). That is an extra install step and an extra thing that must stay compatible across Grafana upgrades.
- VictoriaMetrics is a **single-vendor** project with a commercial Enterprise edition. Prometheus and Thanos are CNCF-governed. The list of Enterprise-only features shows which way the line has historically moved. This is not an accusation — it is a governance property that should be weighed the same way a licence is, and it is a real difference from the alternative.

---

## Consequences

### Positive

- One process to run, restart, back up and reason about for metrics; one for logs.
- No object storage on the query path — graphs work during an object-storage incident.
- Prometheus-operator CRDs from third-party charts work without modification.
- PromQL dashboards render; Grafana and Alertmanager knowledge transfers unchanged.
- Alerting routing, silences and receivers are stock Alertmanager, so nothing about on-call is novel.
- Plausibly small enough to run on nodes sized for tenant workloads rather than requiring a dedicated node — **plausibly**, not measured.

### Negative

- **No downsampling** without an Enterprise licence, so there is no cheap tier and long retention has no affordable path.
- **Retention is local disk**, which is a per-gigabyte-month cost that grows linearly and has a hard ceiling at volume size.
- **MetricsQL differences are silent numeric differences.** Copied thresholds are suspect.
- **LogsQL is a language nobody arrives already knowing**, and community log dashboards assume Loki.
- **Converter traps** (orphaned scrapes, overwritten edits) are live hazards requiring explicit configuration.
- **Vendor benchmarks are unreproduced**, so the footprint argument is currently an assumption.
- **Smaller community and single-vendor governance.** Recruiting, hiring and "has anyone hit this before" all get harder.
- **Traces remain on Tempo**, so the stack is not homogeneous and there are two operational models in one observability system.

### Neutral

- `vmagent` speaks Prometheus remote-write, so the exit path is real: point it at something else and dual-write during a migration. The escape hatch exists and is the standard protocol, not a bespoke one.
- Grafana is retained regardless of which alternative wins, so the UI decision is independent of this one.

---

## Alternatives considered

### A. `kube-prometheus-stack` alone (Prometheus + Alertmanager + Grafana, local storage)

The honest baseline, and closer to viable than it usually gets credit for. Prometheus documents that *"a limitation of local storage is that it is not clustered or replicated. Thus, it is not arbitrarily scalable or durable in the face of drive or node outages and should be managed like any other single node database"* — but also, in the same document, that *"With proper architecture, it is possible to retain years of data in local storage"* ([Prometheus, Storage](https://prometheus.io/docs/prometheus/latest/storage/)).

So "Prometheus cannot do long retention" is folklore, not documentation, and this ADR does not use that argument.

**Rejected because:** it has no logs story at all, so a second system is needed regardless — at which point the comparison is "Prometheus + Loki" (option B) rather than this. Footprint at equal retention is the secondary reason, and it is a vendor-claimed advantage rather than a measured one.

### B. Prometheus + Thanos + Loki

The ecosystem default and the strongest alternative.

**In its favour:** CNCF governance; free downsampling on by default; object storage as the cheap tier; by far the largest pool of people who have operated it; every chart, dashboard and tutorial assumes it.

**Rejected because:** four to six components plus a bucket to observe a cluster smaller than the observability stack; a Compactor that is a singleton by default with a manual-recovery failure mode; object storage on the query path; and the high-cardinality log question (constraint 7) requires up-front schema design that this kit cannot impose on tenant workloads.

**This is the option to switch to** when retention crosses roughly a year — see the revisit triggers.

### C. Grafana Mimir

**Not evaluated in depth.** It is a plausible middle point — Prometheus-compatible, designed for long retention on object storage, with a monolithic mode. Recording it as unevaluated rather than pretending it was weighed and rejected.

### D. Hosted / SaaS observability

**Rejected**, for the same reason the dashboard has no public route. An observability backend is a complete copy of everything the platform has ever recorded: request paths, client addresses, user identifiers, service topology, database statements, and credentials somebody logged by mistake. Shipping that off-cluster is a data-processing decision, not a convenience decision, and it changes who is in scope for whatever regime the tenants are under.

Secondary: SaaS pricing is typically proportional to active series or ingested volume — the two numbers a platform operator least controls, because a tenant can multiply them with one bad label.

---

## Revisit triggers

Re-open this ADR when any of the following becomes true. These are the conditions under which the decision is **wrong**, not merely suboptimal:

1. **Retention requirement exceeds about twelve months.** Downsampling economics flip and Thanos wins outright. This is the most likely trigger.
2. **Cardinality outgrows one node's RAM.** `vmsingle` is a vertical-scaling design; at that point VictoriaMetrics cluster and Thanos become a real comparison rather than a foregone one.
3. **More than one operator.** The decision is heavily weighted by "one person has to hold this in their head". With a team, ecosystem familiarity is worth more than component count.
4. **VictoriaTraces (or equivalent) matures**, removing the odd component out and making the stack homogeneous — which is worth re-examining in both directions.
5. **Licensing moves a depended-upon feature behind Enterprise.** Track the Enterprise feature list as a dependency, not as marketing.
6. **The vendor benchmarks are reproduced and found not to hold** on this workload. Then the footprint argument evaporates and only component count remains, which is a weaker case.

---

## Verification status

**Nothing in this ADR has been measured on a running cluster.** Every performance figure is vendor-published; every behavioural claim is documentation-published and cited. Consistent with the repository's stated posture, the first real pilot is the verification, and this section should be replaced with dated measurements when it exists.

Specifically unverified:
- All RAM and disk comparisons.
- Whether `vmsingle` fits alongside tenant workloads on the node sizes this kit provisions.
- Whether the operator's Prometheus conversion covers every CRD shipped by the charts this kit consumes.
- Anything about VictoriaTraces, which was not evaluated at all.

---

## Sources

- [VictoriaMetrics — Single-server](https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/)
- [VictoriaMetrics — MetricsQL](https://docs.victoriametrics.com/victoriametrics/metricsql/)
- [VictoriaMetrics — Enterprise features](https://docs.victoriametrics.com/victoriametrics/enterprise/)
- [VictoriaLogs](https://docs.victoriametrics.com/victorialogs/)
- [VictoriaMetrics Operator — Prometheus integration](https://docs.victoriametrics.com/operator/integrations/prometheus/)
- [Thanos — Getting Started](https://thanos.io/tip/thanos/getting-started.md/)
- [Thanos — Compactor](https://thanos.io/tip/components/compact.md/)
- [Grafana Loki — Deployment modes](https://grafana.com/docs/loki/latest/get-started/deployment-modes/)
- [Grafana Loki — Labels and cardinality](https://grafana.com/docs/loki/latest/get-started/labels/)
- [Prometheus — Storage](https://prometheus.io/docs/prometheus/latest/storage/)
