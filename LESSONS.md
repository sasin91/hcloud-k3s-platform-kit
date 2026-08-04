# Lessons

Things that were true and expensive to learn, stated as mechanisms rather than stories.

Each is written so a stranger can verify it. Where a public source exists it is cited; where one does not, the lesson is stated as something you can reproduce or check yourself. There are no anecdotes here, and that is deliberate — a mechanism travels, an anecdote does not.

Most of these are also encoded twice more: as a comment at the line they constrain, and where the check is mechanical, as a guardrail that fails. Prose gets skipped. A red build does not.

**The shape almost all of them share:** nothing crashed. `apply` succeeded, `plan` was clean, the pods were green. Every failure below was silent, and several were silent for months.

---

## Configuration that lies

### An unpinned dependency is a latent breaking change

A module or chart reference without an explicit version constraint means the next `init -upgrade` can cross a major version boundary. This is not hypothetical for any actively-maintained dependency: a major release can rename variables, change the shape of nested inputs, and raise the minimum tool version, all in one tag.

*Guardrail: fail if any module or chart reference lacks a version constraint.*

### Duplicate keys in a values file silently discard the earlier block

YAML has no duplicate-key error in most parsers — the later key wins. A values file can therefore contain a carefully-written `resources` block that never reaches anything, because a second occurrence of the same parent key appears forty lines below it.

*Guardrail: fail on duplicate keys in any values file.*

### Reusing values loses values

Upgrading a Helm release with `--reuse-values` merges against what is already installed, not against what is in your repository. Values removed from the file stay in the release; values added may not arrive. The committed file must be the entire source of truth and be passed in full.

*Check: compare the release's user-supplied values against the committed file. If they differ, one of them is fiction.*

### A workaround outlives its cause

The comment explaining why a constraint exists is exactly what makes it look deliberate long after it stopped being true. And every dependent workaround inherits the same dead justification — a single replica justifies disabling a disruption budget, and the disruption budget stays disabled after the replica count could safely have risen.

> A `TODO` beside a constraint is not the same as the constraint being revisited. When you remove a cause, go and hunt its effects.

### State that exists only on one machine will be lost

Local state, gitignored, with no remote backend is a configuration that will eventually stop being able to manage what it built.

> A configuration that cannot manage what it built is worse than no configuration at all. It looks authoritative while being inert.

Versioning on the state store is the recovery path for corruption, which is a different failure from loss and more common. Client-side encryption makes remote storage a storage decision rather than a secrets-exposure decision — [OpenTofu encrypts state and plan files at rest in any backend](https://opentofu.org/docs/language/state/encryption/).

### Opposite lifecycles must not share a bucket

State wants versioning kept indefinitely. Periodic snapshots want expiry, because unbounded retention is a bill that grows forever. Put both in one bucket and the day someone adds an expiry rule to stop paying for old snapshots, they are one prefix pattern away from expiring state history instead.

The failure is silent, discovered only when recovery is attempted — and what it destroys is the recovery path.

---

## Capacity

### "Supported" is not "available"

Cloud APIs distinguish the instance types a location *supports* from those it currently *has*. Configuration written against the former validates, plans, and applies cleanly — then fails at the moment you need to scale, which is by definition the worst moment.

> **Availability is not a property you check once.** A pool created when its type was in stock keeps that type in its configuration forever. Stock changes; the configuration does not.

**This was demonstrated on this kit, by this kit, within twenty-four hours.**

On 2026-08-03 the provider's API reported the entire cost-optimised instance line available in one datacentre, and those types became the kit's defaults. On 2026-08-04 an apply of that configuration failed with `resource_unavailable` — and a fresh query showed **every type in that line unavailable in all three datacentres of the region**, not merely the one. Two nodes had already been created before stock ran out, so the failure landed *mid-apply*, with some infrastructure built and some not.

Three things that episode settles, which no amount of reading would have:

- The window is **days, not quarters**. A dated observation about availability is stale almost immediately, and documentation stating what is available is wrong by construction.
- Stock exhausts across a **whole product line at once**, not per type. Choosing a different size within the same family is not a fallback.
- A fallback on a *different* line was available throughout. Cross-line beats cross-size, and cross-location did not help at all here — the shortage was regional.

The check that catches this must therefore run **before every apply**, not only on a schedule, and it must be cheap enough that nobody is tempted to skip it.

Corollary: **treat a fallback that has never once been provisioned as unproven, not as insurance.** A second pool naming an equally unavailable type protects nobody, and its presence stops anyone looking.

*Guardrail: fail if any declared pool names a type not currently available in its location — and run it on a schedule, not only before apply.*

### A taint nothing tolerates makes a pool that never scales

If an autoscaling pool carries a taint no pending workload tolerates, the autoscaler can never simulate a pod fitting there. The pool will not scale up regardless of demand, and nothing reports an error.

### A derived identifier is only as stable as its input

Numbering things by index means deleting one silently renumbers every one after it. If users depend on the identifier, it must be declared, not computed — computation has inputs, and inputs change.

---

## Delivery

### Applying once proves nothing about tomorrow

An apply-only pipeline demonstrates correspondence between code and cluster at exactly one instant. Everything after that is assumption. This is the failure behind configuration that drifted, values files never applied, and settings that were never real.

### An apply-only pipeline never removes anything

Resources deleted from the repository keep running indefinitely. A resource nobody remembers is a resource nobody patches — and the ones that survive deletion are disproportionately the ones somebody deliberately tried to remove.

### A reconciler's default identity is usually the most privileged in the cluster

Where a reconciliation resource does not name a service account, it is typically applied by the controller's own identity — and controllers are commonly bound to cluster administrator. Anyone able to create such a resource in any namespace therefore acts as cluster administrator, regardless of their own permissions.

> A namespace does not contain a controller acting *on that namespace's behalf*. The boundary is the identity applying the manifests, not where they land.

Flux ships [lockdown flags](https://fluxcd.io/flux/installation/configuration/multitenancy/) that close this; they are not on by default.

*Guardrail: fail if any controller lacks the lockdown flags, or any tenant reconciliation omits a service account.*

### Ordering is a correctness property, not a convenience

A resource whose custom definition has not been established yet fails, retries, and eventually succeeds. That reads as flakiness rather than as a missing dependency edge, which is how ordering bugs survive for years.

### One secret cannot be encrypted, and it should be obvious which

With encrypted secrets in the repository, exactly one secret is applied by hand: the key everything else is encrypted to. Everything else lives encrypted in git. Stating that plainly is worth more than any amount of secrets documentation, because it tells a reader the entire trust model in one sentence.

---

## Security

### Two layers where the inner one is disabled is one layer wearing a costume

Defence in depth is a claim about what happens when the outer layer fails. It is only meaningful if the inner layer is enabled *while* the outer one is working. A permissive inner configuration behind a single gateway is not two layers.

### An observability dashboard is a query interface, not a set of charts

This is consistently underestimated, so state the consequences concretely rather than saying "secure your dashboards":

- Explore-style features run arbitrary queries against every datasource. Read access to the dashboard is read access to everything the platform has ever recorded.
- Logs carry request paths, client addresses, user identifiers — and routinely, by accident, credentials somebody logged by mistake.
- Traces are the architecture diagram nobody wrote down: service names, call graphs, database statements, timings.
- Datasource definitions point at cluster-internal addresses, making the dashboard a queryable window onto services otherwise unreachable from outside.
- An observability stack is deliberately cross-cutting. There is usually no per-tenant isolation inside it, so one credential is every tenant at once.

The safest configuration is the one that does not exist: ship no route, and make exposure an explicit act with its own documentation.

### Prose describing a security posture drifts from the posture

A comment asserting that a component runs read-only is not evidence that it does. Comments are not checked; configuration is. Where a security property is mechanically checkable, check it.

### Enforce actions, report absences

> **Security boundaries fail. Standing configuration reports.**

Enforce at admission when a tenant's *action* can take something from another tenant at the moment it happens — claiming a hostname, obtaining a certificate for a name it does not own. Generate and check *standing configuration*, where the failure is an absence rather than an act.

An absent quota is a gap for somebody to close. An absent privilege boundary is a hole to stop. Conflating them trains people to ignore both, and a wall of warnings is indistinguishable from no warnings at all.

---

## Tenancy

### A CNI that *can* enforce policy is not a cluster that *has* any

Policy enforcement is the default almost everywhere. Written policy is not. An unpoliced multi-tenant cluster lets every tenant reach every other tenant's database over the pod network, and looks completely healthy doing it.

The gap is authorship, not capability — which means changing the network plugin does not fix it, and would hide the real problem behind a more capable tool.

### In the generator, or nowhere

Measured across a mature multi-tenant cluster: guarantees generated by the provisioning path were present in over ninety percent of namespaces. Guarantees documented as recommendations were present in **none**.

> A suggestion in the template that creates the namespace lands. The same suggestion in documentation does not. The axis is not enforcement versus suggestion — it is *in the generator versus not in the generator*.

### Generation is prospective; only a check finds what predates it

A generator covers everything created after it learned to, and never goes back. The tenant provisioned before a guarantee existed stays uncovered forever, and no improvement to the template will ever find it.

> A check that finds nothing on a mature cluster is more likely broken than clean.

### A default that only works once is a trap

Where tenants need a unique value — a port, an address, a name — giving the first one the conventional default makes the template appear to have a sensible default. The second tenant hits a collision whose failure reads as a bug rather than a design limit. Worse, the first tenant's success is what convinces everyone the pattern is fine.

> In a multi-tenant system the first tenant is not the common case. It is the least representative one.

### A value consumed twice must be declared once

Two places that *can* disagree eventually will, and the failure surfaces far from the divergence. If a port appears both in the service exposing it and in a record advertising it, both must render from one declared value — and populating that record must be part of generating the tenant, not a manual step afterwards.

---

## Networking

### The container runtime is not a cluster citizen

It runs on the host, outside cluster DNS and outside cluster trust. Any attempt to point it at an in-cluster service requires host-level surgery — a hardcoded cluster address, a hosts-file mutation, or disabled certificate verification. Usually all three, because each alone is insufficient.

Give the service a real name and a real certificate instead. Traffic hairpins out and back, costing milliseconds and, on a provider with generous included transfer, nothing.

### A hardcoded cluster address in node configuration is a delayed fault

Cluster IPs are stable until the service is recreated. Recreate it once, months later, and every node points at an address nobody is listening on. The symptom is cluster-wide image pull failure; the cause is several layers away, in a file nobody thinks of as configuration.

### A reverse proxy is a shared restart domain

It terminates every connection passing through it, so restarting it — a configuration change, an upgrade, a node drain for unattended patching — destroys them all at once.

For HTTP this is invisible: connections are short and clients retry. For a protocol holding long-lived sessions it means every user disconnected, every time anyone touches ingress.

> Do not put long-lived connection protocols behind the same proxy as short-lived ones. They have incompatible tolerance for restarts, and the short-lived ones set the schedule.

### An egress nodepool without an egress mechanism is decoration

A dedicated node holding a stable outbound address does nothing unless either the network plugin implements an egress gateway, or the workload is pinned to that node. Otherwise traffic leaves from wherever the pod happens to be scheduled, and the pool, the address and its reverse DNS are all inert.

If you ship the pool, ship the mechanism that makes it load-bearing — or do not ship the pool.

---

## Backups

### A backup must assert on its output, not its exit status

Every widely-used backup tool has at least one path where a run reports success having captured nothing: a selector matching no resources, an unmounted volume silently skipped, a zero-volume run exiting clean, a dump piped to standard input producing a valid empty archive. In several cases upstream is still debating whether that should be considered a bug.

> A green, empty backup is worse than a failed one, because it silences the alarm that would have caught it.

This cannot be bolted on afterwards. It belongs in the job.

### Restore legibility beats backup mechanics

Evaluate backup tools on what a *restore* concretely involves, not on how the backup is taken. A restore that streams into a running service — no volume to mount, no workload to scale down, no contention for a single-writer disk — is worth more than a more elegant capture path.

### Check whether your storage has snapshots at all before designing around them

Some providers' block storage supports no snapshots whatsoever. That eliminates every snapshot-based backup path — the CSI route in cluster-wide tools, and the snapshot modes in database operators — before evaluation begins, and collapses most of the apparent difference between candidates into file copy or logical dump.

Verify by checking whether the driver advertises the capability and ships a snapshot class, not by reading marketing pages. Some providers use "snapshot" for a different product entirely.

---

## Scale

### Orchestration does not confer scalability

A single-authoritative process gains nothing in capacity from being containerised, replicated or autoscaled. It gains scheduling and restart handling. If the workload holds its state in one process's memory, the ceiling is that machine, and the honest scaling axis is vertical.

This is worth stating because platform tooling implies the opposite by default.

### Where entity identity is a pointer, distribution is foreclosed

In a single-authoritative-process simulation, the identifier for a peer entity is effectively a machine address. Any design where entity identity is a pointer into one process's heap — and any generator of persistent identifiers living in process memory — has already ruled out multi-process operation.

Two such processes sharing one database is not sharding. It is data loss, because both hand out identical identifiers from counters seeded independently at startup.

### Persisting state makes it durable, not shared

> The database is a checkpoint, not a channel.

Subsystems can be fully database-backed and still be per-process, because they are loaded once at startup and mutated only in memory thereafter. Nothing polls. Durability and sharing are different properties, and the presence of the first is routinely mistaken for the second.

### Getting the architecture right does not guarantee that adding machines adds capacity

The research effort that came closest to making distribution transparent — explicit ownership, an indirection layer, runtime repartitioning, portable sessions — was cancelled after reporting that additional machines *lowered* the capacity of the overall system. [A Note on Distributed Computing](https://scholar.harvard.edu/files/waldo/files/waldo-94.pdf) explains why hiding the local/remote distinction fails: latency, memory access, concurrency, and partial failure.

### Benchmarks can anti-correlate with your workload

For a branchy, pointer-chasing simulation loop whose working set fits in a large cache, conventional single-thread benchmark scores predict the wrong winner. A stacked-cache part measured substantially faster on a simulation tick while scoring *lower* on the popular single-thread benchmark.

> Pick the benchmark whose access pattern resembles your workload, or measure your own. A general-purpose score is a general-purpose answer.

Related: for such a workload, a many-core server part can be *worse hardware* than a desktop part, because memory latency is materially higher.

---

### A reconciler that waits for health cannot heal what keeps it unhealthy

A reconciliation that waits for its resources to become ready is the correct
default — it is the difference between proving a rollout worked and proving the
API server accepted some YAML. But it creates a deadlock the moment a resource
becomes *permanently* unhealthy.

Observed: a release could not install because it lacked permission. Its parent
reconciliation blocked, waiting for health. The fix was committed and the source
was fetched — and the reconciliation never applied it, because it was still
waiting on the previous attempt. Its last-applied revision stayed empty while the
source sat several commits ahead.

> Waiting for health makes a rollout honest. It also means a broken rollout
> cannot be repaired by committing the repair. Something outside the loop has to
> break the tie.

Practical consequences: set a timeout that actually expires, and know before you
need it how to intervene — suspend the reconciliation, patch the live resource,
or delete it so the next pass recreates it. A GitOps system where the only
documented remedy is "commit a fix" has no answer for the case where committing
does nothing.

### A dependency is fetched by a tool with its own opinions

Pinning line endings in your own repository protects your own files. It does
nothing for source a build tool fetches on your behalf — and that fetch runs
under whatever configuration the local machine happens to have.

Observed: infrastructure tooling downloads a module over git; git on one platform
rewrites text files to CRLF by default; one of those files is a policy source
compiled on the remote host; the compiler rejects the carriage return. Every
server is created successfully, the provider reports them healthy, remote access
works, and the orchestrator that was supposed to run on them silently never
starts. The only visible symptom is an opaque provisioner error two layers above
the real one.

> The reproducibility of a build is bounded by the least reproducible thing it
> fetches. Pin what you fetch, and pin **how** you fetch it.

### A name you did not choose is an input you did not know you had

A delivery tool that installs charts on your behalf has to call the release
something. When you do not name it, it derives one — commonly from the object's
name and the namespace it targets. That derived string is then an input to every
name the chart generates: services, jobs, labels, the lot.

So moving a release between namespaces — a refactor with no functional intent
whatsoever — renames things that other components address by name. Observed on a
live cluster, one such move broke three things at once:

- Two Services were renamed. The components exporting to them kept exporting to
  the old names, which no longer resolved. **Nothing errored.** The exporters
  reported healthy, the receiver reported healthy, and the data was simply gone.
- One chart's generated label crossed 63 bytes and became invalid.

The second is worth dwelling on. The over-long string was a *Job pod label*,
which is capped at 63 bytes, but the same string was also a *ServiceAccount
name*, which is capped at 253 — so it was simultaneously legal and fatal
depending on which field it landed in. The release wedged in `uninstalling` and
could not remediate, because the pre-delete hook it had to run in order to
uninstall was itself the invalid object. Un-installable and un-removable, from
one derived name being 39 characters instead of 26.

> Name the things whose names other things depend on. A generated name is not a
> name — it is a value that happens to be stable until someone moves the object
> that generates it.

### Deleting a controller strands the objects it was finalising

Custom resources carry finalizers, and the controller that owns them is the only
thing that clears them. Remove the controller first and every one of its
resources becomes undeletable: the delete blocks forever, waiting on a reconciler
that no longer exists. `kubectl delete` does not fail — it hangs, which reads as
a slow cluster rather than a permanent one.

The escape is to strip the finalizer by hand, which is safe here only because the
cleanup it guarded is moot once the controller is gone.

> Order of teardown is part of the contract. Anything that installs a controller
> alongside its custom resources owns the reverse order too.

### Force-deleting the release object to escape a wedged release makes it worse

The tempting escape from a release stuck mid-uninstall is to delete the
declarative object and let the reconciler recreate it. Doing so interrupts the
uninstall the tool was already attempting, and the storage record is left saying
`uninstalling` — a state from which the tool will neither install nor remove,
because it believes a removal is in progress that nothing is progressing.

The resources themselves may be perfectly healthy the whole time. Observed: every
pod of the release Running and ready, while the release it belongs to could not
be reconciled at all.

The recoverable order is: delete the *storage record* first, confirm the tool now
reports the release absent, and only then let the reconciler install. Deleting the
declarative object is the step that removes your ability to do that cleanly.

> A reconciler's escape hatches are ordered. Reaching for the most forceful one
> first usually destroys the state the gentler ones needed.

## Two that are about people

### The first success is what convinces everyone the pattern is fine

This appears twice above in different clothes — the tenant that got the standard port, the workaround whose cause was removed. In both cases the thing that made the problem durable was that it worked the first time.

### Documentation drifts; generators and checks do not

Every lesson here that was originally written as a comment, a runbook entry or a README line was, at the point of measurement, wrong. The ones encoded as generated resources or automated checks were right.

That is not an argument against writing things down. It is an argument for writing them down **in the place that executes**.
