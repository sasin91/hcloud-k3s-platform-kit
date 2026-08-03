# platform/

What runs on the cluster once the cluster exists. Provider-independent by
intent — the coupling to a particular cloud lives in `infrastructure/`, and the
handful of lines here that could not avoid it are marked where they occur.

**Nothing in this directory has been verified against a running cluster.** Chart
and definition versions were read from upstream release APIs and chart indexes
on 2026-08-03 and are recorded at the reference that uses them. Everything else
is reasoning, and reasoning is not evidence.

---

## Two stages, and why they are two

```
platform/controllers/            things that install definitions and controllers
  crds/                            committed custom resource definitions
  releases/                        the controllers themselves
platform/configs/                things that require those definitions to exist
  admission/                       the one thing that is rejected, not reported
platform/observability/          its own two stages, reconciled after these
platform/addons/                 nothing installed; see its README
```

A resource whose custom definition has not been established yet fails, retries,
and eventually succeeds. That reads as flakiness rather than as a missing
dependency edge, which is how ordering bugs survive for years. So the edge is
declared. Inside `controllers/` there is a second edge for the same reason: the
Gateway API definitions must exist before the ingress controller and
cert-manager start, and a HelmRelease cannot declare a dependency on a
Kustomization — `spec.dependsOn` on a HelmRelease means other HelmReleases — so
the dependency is drawn between two Kustomizations instead.

The cluster's own reconciliation supplies the outermost edge. Its shape, in
`clusters/<name>/`:

```yaml
# platform-controllers → platform-configs → tenants
# Each stage waits for the previous one to be healthy, not merely applied.
spec:
  path: ./platform/controllers
  prune: true
  wait: true
  serviceAccountName: kustomize-controller
---
spec:
  path: ./platform/configs
  dependsOn:
    - name: platform-controllers
  prune: true
  wait: true
  serviceAccountName: kustomize-controller
```

Every Kustomization names a service account. With the multi-tenancy lockdown
flags set, one that names none is applied by a powerless identity; without those
flags it would be applied by the controller's own, which upstream binds to
cluster administrator.

---

## What is installed, and at what version

```
cert-manager                 chart 1.21.1   app v1.21.1
  oci://quay.io/jetstack/charts — latest non-prerelease from the
  cert-manager/cert-manager releases API, cross-checked against the registry's
  tag list. The HTTP index publishes the same chart as "v1.21.1"; the OCI tag
  has no "v".

traefik                      chart 41.1.1   app v3.7.9
  oci://ghcr.io/traefik/helm — latest chart release, confirmed present in the
  registry's tag list.

metrics-server               chart 3.13.1   app 0.8.1
  https://kubernetes-sigs.github.io/metrics-server/ — highest version in the
  chart index. Application 0.9.0 exists upstream with no chart release yet.

Gateway API                  v1.6.1, standard channel
  Pinned GitRepository tag. Channel confirmed from the definitions' own
  gateway.networking.k8s.io/channel annotation, not from documentation.

system-upgrade-controller    v0.20.1
  Release assets committed byte-identical under controllers/crds/ and
  controllers/releases/. The manifests checked into that repository at the same
  tag still name image v0.14.0, so the release artifact is the only
  version-consistent form of them.
```

No reference is a range or a channel. A range such as `1.21.x` is an unpinned
reference wearing a version's clothes, and there is a check that fails the pull
request on one. The single deliberate exception is the k3s release channel in
`configs/k3s-upgrade-plans.yaml`, where the point is to receive patch releases —
it is commented as such, and the alternative exact pin is one line below it.

---

## Decisions this directory encodes

**cert-manager is the only certificate authority.** The ingress controller runs
no ACME resolver, and `certificatesResolvers` is explicitly empty so that a
check can assert it. An ingress controller's built-in ACME client stores
certificate state locally, so replicas race and reissue against each other;
everyone's workaround is a single ingress replica, which makes the most
traffic-critical component in the cluster a single point of failure. cert-manager
issues into a Secret that any number of replicas read, so the constraint goes
away rather than being managed.

**The ingress runs more than one replica, with a disruption budget and
anti-affinity across nodes.** When you remove a cause, go and hunt its effects:
the single replica is what justified disabling the disruption budget, and the
budget usually stays disabled long after the replica count could safely have
risen. This cluster applies unattended operating-system updates and drains nodes
to reboot them, and a drain evicts a workload with no disruption budget with no
guarantee at all.

**HTTP-01 by default, DNS-01 shipped commented out.** HTTP-01 needs no
credentials, so a stranger with a fork and a hostname gets a certificate.
DNS-01 is not an optimisation — it is required for a wildcard certificate, and
required behind any proxying CDN, where the challenge never reaches the cluster.
Its cost is a credential that can edit a DNS zone.

**Gateway API for HTTP, and nothing else for HTTP.** The Ingress and
IngressRoute providers are switched off, not left on as a convenience: tenant
hostnames are authorised by the admission policy in `configs/admission/`, which
targets route objects, and a second enabled route source would be a second,
unpoliced way to claim the same name.
Cross-namespace *attachment* is governed by each listener's `allowedRoutes`;
`ReferenceGrant` governs cross-namespace *references* and is written by the
namespace giving something up. Both are needed and neither substitutes for the
other.

**A `Retain` storage class beside the provider's deleting default.** The default
destroys the volume when the claim goes, and the ways a claim goes are ordinary
— a namespace deleted, a chart uninstalled, a path renamed under a pruning
reconciler. This provider's block storage supports no snapshots, so there is no
second copy to fall back on.

**Upgrades are servers first, one at a time, then agents.** The ordering is a
`prepare` dependency rather than a schedule, because a schedule is a guess about
how long something takes.

**The autoscaler priority expander ships with only one pool active.** Enabling
the cross-location fallback is then one uncommented block in the infrastructure
layer and nothing else — the ordering that keeps the cheap pool winning is
already in the repository, rather than being a second thing to remember at the
moment capacity is short.

---

## Preconditions in the infrastructure layer

The cluster module installs several of these components itself. Two
installations of one thing do not merge; they take turns, and the loser is
whichever reconciled last. Before this layer is applied, the module must be told
to stand down:

```
ingress_controller               = "none"    # default "traefik"
enable_cert_manager              = false     # default true
enable_metrics_server            = false     # default true
enable_system_upgrade_controller = false     # default true
cluster_autoscaler_extra_args    = ["--expander=priority"]
```

The first four are ownership. The fifth is the one that is not: without it the
priority expander ConfigMap in `configs/` is read by nobody, and nothing reports
that it was ignored.

`enable_kured` stays as it is — reboot orchestration after operating-system
updates belongs to the node layer, and this directory upgrades Kubernetes only.

---

## Before the first apply

Everything below is a placeholder that will apply cleanly and behave wrongly.

```
controllers/stages.yaml     sourceRef name, if the fork's Flux source is not
                            called "flux-system"
releases/ingress.yaml       load balancer location and type — both must be
                            currently AVAILABLE in that location, not merely
                            supported
configs/cluster-issuers.yaml   two email addresses; the ACME account is
                            registered against them permanently
configs/gateway.yaml        the listener hostname, and the issuer annotation —
                            start on letsencrypt-staging, move to letsencrypt
                            once a certificate has actually been issued
configs/k3s-upgrade-plans.yaml   the release channel, which must be the minor
                            the cluster is already running
configs/gateway.yaml and configs/admission/   the "platform.example.com/"
                            prefix on the tenant label and the delegated-domains
                            annotation. The Gateway's allowedRoutes selector and
                            the admission binding's namespaceSelector must name
                            the same label as the tenant template writes — a
                            namespace that can attach a route but falls outside
                            the policy binding may claim any hostname it likes
```

The two autoscaler patterns in `configs/cluster-autoscaler-priority-expander.yaml`
match on a suffix so they survive any cluster name, but they still have to match
the pool *names* declared in the infrastructure layer. A pattern that matches
nothing is not an error — the expander ignores it and picks a group at random.

---

## Not here, on purpose

No private registry, no database operator, no CNI, no observability stack, and
no egress pool. The first two are in `platform/addons/` as documentation only.
The CNI is the distribution's flannel, which already enforces NetworkPolicy —
installing a second one would replace a working default with an eBPF debugging
surface most adopters will never need. Observability is its own layer with its
own reasoning about exposure. The egress pool is absent because a floating
address without an egress-gateway mechanism is inert decoration.
