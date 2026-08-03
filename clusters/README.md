# clusters/

The GitOps delivery layer. Flux reconciles this directory; everything else in
the repository is reached from here.

---

## The trust model, in one sentence

> **The age key is the only secret created by hand, and the only one that
> cannot be encrypted. Everything else lives encrypted in git.**

That is the whole thing. It is worth more than any amount of secrets
documentation, because it tells a reader what to protect and what not to worry
about without them having to read anything else.

Two footnotes, stated here rather than left to be discovered:

- A **private** fork adds one further hand-created secret — a read-only,
  single-repository deploy key — because Flux must read the repository before
  it can decrypt anything inside it. A public fork needs no git credential at
  all, and the sentence above is then literally true.
- Cluster creation happens before Flux exists, so the provider token and the
  object-storage credentials belong to the infrastructure tool and never enter
  the cluster. They are outside this boundary, not an exception to it.

---

## Day zero

```bash
# 1. Generate the root of trust. The private half goes to a password manager.
age-keygen -o age.agekey

# 2. Put your public key in .sops.yaml (two occurrences, both placeholders).

# 3. Point clusters/production/flux-system/gotk-sync.yaml at your fork
#    (spec.url and spec.ref.branch).

# 4. Install Flux from the committed manifests.
kubectl apply -k clusters/production/flux-system

# 5. Give the cluster the key everything else is encrypted to.
kubectl create secret generic sops-age \
  --namespace flux-system --from-file=age.agekey=age.agekey
```

Step 4 may fail the first time with `no matches for kind "Kustomization"`.
`kubectl apply -k` sends the custom resource definitions and the custom
resources in a single pass and does not wait for the definitions to be
established. Run it again. This is a property of `kubectl`, not a sign that
anything is wrong.

No personal access token. No admin-scoped credential. Nothing written back into
your repository on your behalf. You can read exactly what will be applied
before applying it, and the Flux version is a line in your own diff.

---

## The Flux component manifest is currently a placeholder

`clusters/production/flux-system/gotk-components.yaml` **is not the real
manifest.** It holds a sentinel resource that makes `kubectl apply -k` fail
loudly, and comments explaining why.

Generate the real one before using this repository:

```bash
./scripts/generate-flux-components.sh
```

The script pins the version and the component list, and documents the exact
`flux install --export` invocation. Do not hand-write the file, and do not
substitute the `install.yaml` asset attached to a Flux release — that asset is
the all-components superset and would install three controllers this kit does
not run.

**Why the manifest is committed rather than bootstrapped.** Committing it is
the end state `flux bootstrap` reaches anyway; reaching it once, as the
maintainer, means no fork ever needs a token or gets written to. The cost is an
obligation: the file goes stale unless somebody regenerates it, which is
mechanical and therefore belongs in continuous integration as a scheduled
comparison that opens a pull request.

---

## Layout

```
clusters/production/
  flux-system/
    gotk-components.yaml   the controllers, generated and committed
    gotk-sync.yaml         the repository, and the root Kustomization
    lockdown-patches.yaml  multi-tenancy flags for kustomize-controller
    kustomization.yaml     the only thing applied by hand
  infrastructure.yaml      -> ./platform/controllers
  platform.yaml            -> ./platform/configs
  observability.yaml       -> ./platform/observability
  tenants.yaml             -> ./tenants
```

`production` is a name a fork chooses once. The directory name appears in
exactly one place — `spec.path` of the root Kustomization in `gotk-sync.yaml` —
and a second cluster is a second directory beside it with its own root.

The four target directories are owned by the layers themselves, not by this
one. This file records the paths the graph expects.

Two of those paths look wrong at a glance and are not:

- **`infrastructure.yaml` points at `./platform/controllers`,** not at
  `./infrastructure`. The directory named `infrastructure/` at the repository
  root is OpenTofu. It creates the cluster, it runs before Flux exists, and
  reconciling it would mean pointing a Kubernetes applier at HCL. It is
  deliberately outside this graph.
- **`platform.yaml` points at `./platform/configs`.** `controllers` installs
  the custom resource definitions; `configs` uses them. They are two
  directories because the edge between them has to be declarable, and a
  Kustomization is the only thing that can declare it — a HelmRelease's
  `dependsOn` refers to other HelmReleases only.

`./tenants` must contain its own `kustomization.yaml` listing the tenants that
are enabled. Where a directory has none, kustomize-controller generates one by
scanning, and that scan picks up `tenants/_template` — which is not a tenant.
Its tokens are unreplaced, so what lands is a namespace literally named
`tenant-REPLACE-tenant`, with quota, policy and a backup job attached. It
applies cleanly and nothing reports a problem. The explicit list is also the
offboarding mechanism: remove the line, and pruning removes the namespace.

---

## The reconciliation graph

```
flux-system (root)
   └── infrastructure    CRDs and the controllers that serve them
          └── platform          config that needs those CRDs to exist
                 └── observability     metrics, logs, traces, dashboard
                        └── tenants           namespaces and their guarantees
```

Every edge is declared with `dependsOn`. None is left to retry.

> **Ordering is a correctness property, not a convenience.** A resource whose
> custom definition has not been established yet fails, retries, and eventually
> succeeds. That reads as flakiness rather than as a missing dependency edge,
> which is how ordering bugs survive for years — and it trains people to re-run
> things instead of fixing the graph.

`observability` sits before `tenants` on purpose. Alerting that starts after
the workloads it watches has a blind window, and that window is exactly when a
first install goes wrong.

| Layer | Interval | Retry | Timeout | Wait | Prune | SOPS |
|---|---|---|---|---|---|---|
| flux-system (root) | 10m | 2m | 45m | yes | yes | no |
| infrastructure | 30m | 2m | 10m | yes | yes | yes |
| platform | 30m | 2m | 5m | yes | yes | yes |
| observability | 30m | 2m | 15m | yes | yes | yes |
| tenants | 30m | 5m | 10m | yes | yes | yes |

The root timeout is arithmetic, not a round number: because the layers are
chained, their timeouts are sequential, and 10 + 5 + 15 + 10 = 40 must fit
inside it. Change a layer timeout and change the root's with it.

The root needs no decryption because it applies only Kustomization and
GitRepository objects. Nothing encrypted lives at that level.

---

## Health checks

Every Kustomization sets `wait: true`.

> A reconciler that applies without waiting has only proven that the API server
> accepted the YAML.

`wait: true` holds a Kustomization NotReady until every object it applied
reports healthy, which is what makes a `dependsOn` edge mean *the controllers
are running* rather than *their manifests were accepted*.

No layer carries an explicit `healthChecks` list. Flux ignores that field when
`wait` is true, and a hand-written list of named resources goes stale silently
as a layer's contents change — an assertion that quietly stops asserting is
worse than none.

---

## Pruning

`prune: true` on every Kustomization.

Pruning is most of why Flux was chosen. An apply-only pipeline never removes
anything: resources deleted from the repository keep running indefinitely, and
a resource nobody remembers is a resource nobody patches. The survivors are
disproportionately the ones somebody deliberately tried to remove.

**It is safe here because a cluster built from this kit is born clean.**
Everything in it arrived through this graph, so *not in git* and *should not
exist* mean the same thing.

**It is dangerous in exactly one situation:** pointing a pruning reconciler at
a cluster with accumulated state nobody has inventoried. Adopting an existing
cluster means auditing what is running first — not disabling pruning and
forgetting. Disabling it anywhere requires a recorded reason, and continuous
integration checks for one.

The largest blast radius is `tenants`: removing a tenant directory deletes that
tenant's namespace and everything in it. That is intended — offboarding should
be a merge — and the backup that makes it survivable is generated with the
tenant, by the same template.

---

## Multi-tenancy lockdown

All three flags are patched onto the controllers. The reasoning is in
`flux-system/lockdown-patches.yaml`; the short version:

| Flag | Controllers | Without it |
|---|---|---|
| `--no-cross-namespace-refs=true` | all | a tenant can use another tenant's sources and events |
| `--no-remote-bases=true` | kustomize-controller | cluster state comes from arbitrary remote URLs |
| `--default-service-account=default` | kustomize-controller, helm-controller | **anyone who can create a Kustomization in any namespace is cluster administrator** |

The third is the one that is not optional and the one that is not obvious. A
Kustomization with no `spec.serviceAccountName` is applied by
kustomize-controller's own identity, and upstream binds that identity to
`cluster-admin` through the ClusterRoleBinding named `cluster-reconciler`.

> A namespace does not contain a controller acting *on that namespace's
> behalf*. The privilege boundary is the identity applying the manifests, not
> the namespace they land in.

**What this obliges.** Every platform-owned Kustomization must now name a
service account explicitly — the root in `gotk-sync.yaml` and the four layers
in `clusters/production/*.yaml` all name `kustomize-controller`. Miss one and it
stops reconciling, with an error that points at permissions rather than at the
missing field.

**What the tenant template must respect.** A per-tenant Kustomization lives in
the tenant's own namespace, names the tenant's own service account, and reads
from a GitRepository in that same namespace — cross-namespace references are
refused, and Flux impersonates the named account in the Kustomization's own
namespace, so a tenant Kustomization sitting in `flux-system` is not a boundary
at all.

Two traps worth knowing before editing the patches, both verified locally on
2026-08-03 with the kustomize embedded in `kubectl`:

- A patch file containing several JSON 6902 documents applies **only the first**
  and discards the rest without an error.
- A patch whose target matches **no** resource is silently ignored.

Both mean a lockdown flag can go missing with every command exiting zero. The
guardrail therefore asserts the flags on the built output —
`kubectl kustomize clusters/production/flux-system` — and never on the text of
the patch files.

---

## Encrypted secrets

Encrypted files follow one convention: they end in `.sops.yaml`. The convention
is load-bearing in both directions — a reader knows a file is encrypted without
opening it, and a reviewer notices a secret that is not.

`.sops.yaml` at the repository root encrypts only `data` and `stringData`, so a
diff shows which secret changed and in which namespace, and shows nothing of
what it changed to.

The in-cluster decryptor is kustomize-controller, which has SOPS built in.
Nothing installs `sops` into the cluster. Rotation of the recipient list is
`sops updatekeys` over every matching file — and a removed recipient can still
decrypt every commit already in history, so removal is key rotation on the
underlying secrets, not an edit to a YAML file.

---

## Versions

Every version referenced by this layer, pinned in one place each.

| Component | Version | Declared in |
|---|---|---|
| Flux | v2.9.3 | `scripts/generate-flux-components.sh` |
| source-controller | v1.9.3 | `gotk-components.yaml` (generated) |
| kustomize-controller | v1.9.4 | `gotk-components.yaml` (generated) |
| helm-controller | v1.6.3 | `gotk-components.yaml` (generated) |
| notification-controller | v1.9.2 | `gotk-components.yaml` (generated) |
| sops | v3.13.3 | `.sops.yaml` (comment) |
| age | v1.3.1 | `.sops.yaml` (comment) |

Controller versions are the ones shipped with Flux v2.9.3, observed on
2026-08-03. They are not chosen independently; regenerating the component
manifest is what moves them.

---

## What continuous integration checks here

Each of these is decidable from the repository alone, and each fails the pull
request rather than reporting, because each is a privilege boundary or a
correctness property rather than standing configuration.

- Every controller in the built output carries its lockdown flags.
- Every tenant reconciliation names a service account.
- Pruning is not disabled anywhere without a recorded reason.
- The committed component manifest is not stale against the current upstream
  release — scheduled, and it opens a pull request rather than failing one.

---

## Status

**None of this has been verified against a running cluster.** It is written
from the upstream API schemas for Flux v2.9.3 and from the decisions recorded in
the issue tracker. The two kustomize behaviours noted above were verified
locally against `kubectl`'s embedded kustomize, and say so with a date; nothing
else here has been executed anywhere.
