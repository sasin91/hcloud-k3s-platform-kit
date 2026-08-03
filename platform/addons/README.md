# platform/addons/

Nothing in here is installed. There are no manifests in this directory, and
that is the content of the decision rather than an omission waiting to be
filled in.

**Why nothing is default.** Every component below is load-bearing for somebody
and dead weight for everybody else. A platform kit that installs all of them
hands a first-time adopter a cluster with several controllers they did not
choose, cannot evaluate, and will not upgrade — and the ones nobody remembers
are the ones nobody patches. The bar for the default profile is that a
component is required by something else in the kit, or that its absence breaks a
guarantee the kit makes. None of these clears it.

Each entry states what it costs, because the interesting part of adopting one is
never the installation.

---

## Database operators

The kit shipped a MariaDB operator by default at one point. It was there because
a removed example workload needed it, which is the ordinary way a default
arrives: not decided, inherited. **No database operator is default now**, and
the reasoning applies to all three candidates equally.

**MariaDB operator.** Declarative databases, users, grants and logical backups.
The reason to want it is that it makes a database a resource the same delivery
mechanism can reconcile. The reason not to have it by default is that it is an
operator with its own upgrade path, its own admission surface, and its own
opinions about failover, in a cluster whose block storage has no snapshots — so
its snapshot-based backup modes are unavailable before evaluation even begins,
and what remains is a logical dump you could have taken from a CronJob.

**CloudNativePG.** The strongest restore story of the three: it streams a base
backup into a fresh instance with no volume to mount and no workload to scale
down. Evaluate backup tools on what a restore concretely involves, not on how
the backup is taken — and by that measure this wins. It is still not a default,
because a Postgres operator in a cluster that runs no Postgres is a controller
watching an empty set.

**Valkey.** Usually wanted for cache or sessions rather than as a database, and
usually deployed by the application's own chart. An operator makes sense at the
point where several tenants need managed instances with failover, which is not
where a new cluster starts.

**The constraint all three run into.** One block volume per namespace is
attached to one node at a time. Two stateful workloads in one namespace do not
share it; the second one gets no durable disk. That shapes what a database
deployment can look like here far more than the choice of operator does, and it
is worth knowing before comparing feature matrices.

---

## A private registry

Absent for the same reason it is absent from most working clusters on day one:
it is not one component. It is a hostname, a certificate, credentials, a storage
decision, a garbage-collection policy, and an availability requirement — because
when it is down, nothing schedules.

Two things to carry over if you add one:

**Give it a real name and a real certificate.** The container runtime runs on
the host, outside cluster DNS and outside cluster trust. Pointing it at an
in-cluster service means a hardcoded cluster address, a hosts-file mutation, or
disabled certificate verification — usually all three, because each alone is
insufficient. A hardcoded cluster address in node configuration is a delayed
fault: it works until the service is recreated, months later, and then every
node in the cluster fails to pull an image for reasons several layers away from
the symptom.

**Garbage collection has broken production before.** A cleanup that deletes
manifests no tag points at will happily delete the only manifest for one
architecture of a multi-architecture image, and per-architecture tags are not
the protection they appear to be. Prove that every in-use digest is reachable
from a tag before running anything, and gate on that intersection rather than on
how many bytes a dry run claims to reclaim.

---

## Cilium

The default CNI already enforces NetworkPolicy, so switching does not fix an
unpoliced cluster — the gap there is authorship, not capability, and a more
capable tool would hide it.

What Cilium genuinely adds and nothing else here can express: policy by
hostname. Standard NetworkPolicy restricts egress by address range only, so
"this tenant may reach these hostnames and nothing else" is inexpressible
without it. That matters when tenants are untrusted and run arbitrary code.

The cost is a per-node agent, an eBPF debugging surface, and a careful upgrade
path. **It is also a create-time choice**: the network plugin is selected when
the cluster is built, so this is a decision to make before the first apply, not
one to migrate to later.

---

## Forward authentication for platform interfaces

The observability stack ships with no route at all, and a port-forward is the
documented way in. An observability dashboard is a query interface, not a set of
charts: read access to it is read access to every log line, trace and metric the
platform has ever recorded, including the credentials somebody logged by
mistake.

If it must be exposed, the exposure needs its own authentication in front of it
rather than the dashboard's own login, and that is an identity provider — a
component with its own availability, its own secrets, and its own upgrade path.
It belongs here, as a deliberate act with documentation attached, and not in a
default profile.
