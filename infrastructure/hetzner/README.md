# Infrastructure layer

Builds the cluster. Nothing else.

This configuration produces nodes, a network, a firewall and a working
kubeconfig, and it stops there. Ingress, certificates, observability, tenancy
and backups are delivered by the GitOps layer from manifests you can read in
your own diff — see the repository root.

> **Nothing here has been verified against a running cluster.** Two things have
> been measured and say so where they are configured: the object storage
> supports conditional writes, so native state locking is real; and its default
> checksum behaviour works, so no checksum workaround ships. Everything else is
> reasoned, not tested. Prices, instance types and availability quoted in these
> files are dated observations, not permanent properties.

---

## What is decided here

| Decision | Value | Where the reasoning lives |
|---|---|---|
| Tool | OpenTofu, not Terraform | `versions.tf` — state *and plan* encrypt client-side |
| Cluster creation | `kube-hetzner/kube-hetzner/hcloud`, pinned `~> 3.0` | `main.tf` — consumed, not forked |
| State | S3-compatible object storage, versioned, `use_lockfile` | `versions.tf` |
| Control plane | Three nodes, x86 | `main.tf` |
| Architecture | x86 only | `main.tf` — Arm was unavailable region-wide when decided |
| Autoscaler | One active pool; a cross-location fallback ships commented out | `main.tf` |
| etcd snapshots | On, to a separate expiring bucket | `main.tf` |
| CNI | Flannel. Cilium opt-in only | `main.tf` |
| Egress nodepool | None | `main.tf` — no mechanism, so no pool |

Comments in those files explain *why* at the line that executes. That is
deliberate: prose drifts, and the lesson belongs where the decision is made.

---

## Day zero

### 1. Create an object-storage credential, by hand

In the provider's console. This step is irreducible — the provider's CLI
manages a different storage product entirely and exposes no object-storage
commands. There is no flag for it; do not go looking for one.

### 2. Create the buckets

```sh
export AWS_ACCESS_KEY_ID=…
export AWS_SECRET_ACCESS_KEY=…
export STATE_BUCKET=your-tofu-state
export SNAPSHOT_BUCKET=your-etcd-snapshots
export S3_ENDPOINT=https://fsn1.your-objectstorage.com
export S3_REGION=fsn1

../../scripts/bootstrap-buckets.sh
```

Two buckets, not two prefixes. They carry opposite lifecycle policies — state
keeps versions forever, snapshots expire — and one bucket puts state history
one prefix pattern away from an expiry rule. The script refuses to run if both
names are the same.

### 3. Point the backend at them

The backend block in `versions.tf` takes **no variables** — a backend is
configured before the variable system exists. Either edit the three lines marked
`PLACEHOLDER`, or delete them and use partial configuration, which keeps your
bucket names out of the repository:

```hcl
# backend.hcl — gitignored
bucket    = "your-tofu-state"
region    = "fsn1"
endpoints = { s3 = "https://fsn1.your-objectstorage.com" }
```

```sh
tofu init -backend-config=backend.hcl
```

This is the one place in the kit where the storage endpoint is written twice —
here, and as a bare host for etcd's snapshot config. If you change one, change
the other.

### 4. Fill in the variables

```sh
cp terraform.tfvars.example terraform.tfvars
```

`terraform.tfvars` must be gitignored before you put anything real in it. The
example file ships placeholders that are deliberately invalid — a copied but
unedited file fails validation rather than quietly working — but that is not a
substitute for the ignore rule.

Secrets are better supplied from the environment than from a file living next to
your state:

```sh
export TF_VAR_hcloud_token=…
export TF_VAR_object_storage_access_key=…
export TF_VAR_object_storage_secret_key=…
export TF_VAR_state_passphrase='at least sixteen characters'
```

`TF_VAR_state_passphrase` must be set **before `tofu init`**. Encryption is
configured before any state is read, so there is nothing to prompt from.

Losing that passphrase means losing the ability to read your own state. Store it
wherever you store the age key — same class of secret, same fate.

### 5. Build

```sh
tofu init
tofu plan
tofu apply
```

`tofu`, not `terraform`. Terraform will fail on the `encryption` block, which is
the point: the requirement is enforced rather than documented.

### 6. Tell the autoscaler which pool to prefer

```sh
tofu output -raw autoscaler_priority_expander_manifest | kubectl apply -f -
```

Generated from the same list that configures the pools, so it can never name a
group that does not exist or omit one that does. It is an output rather than a
managed resource because applying it would need a Kubernetes provider configured
from this module's own output — unknown at plan time on a fresh cluster, so the
first `tofu plan` would fail and the fix would be a two-phase apply. Committing
it to the delivery layer instead is equally correct.

Applying it late is safe: when the ConfigMap is absent the autoscaler skips the
priority expander and falls through to `least-waste`. Missing configuration
degrades the choice; it does not break scaling.

---

## Before every apply, and then on a schedule

**Check that every `server_type` is currently *available* in its location — not
merely *supported*.** They are different fields in the provider API. A pool
naming a supported-but-unavailable type validates, plans and applies cleanly,
and then fails at the moment you need to scale, which is by definition the worst
moment.

This is not a one-time check. A pool created when its type was in stock keeps
that type in its configuration forever; stock moves and the configuration does
not. The kit's scheduled guardrail exists for exactly this.

The commented-out fallback pool is subject to the same rule and one more: treat
it as **unproven until it has provisioned a node at least once**. A fallback
naming an equally unavailable type protects nobody, and its presence is what
stops anyone from looking.

---

## Things that are on, and cost money

- **Three control plane nodes.** Three is the smallest HA etcd quorum, and it
  must be odd — two nodes is strictly worse than one.
- **etcd snapshots to object storage**, on a schedule, into a bucket with an
  expiry rule.

Both are defaults here because the alternative is a cluster that looks fine
until the day it does not.

---

## Retention appears twice, on purpose

k3s prunes the snapshots it knows about (`etcd_snapshot_retention`). The bucket
lifecycle rule (`SNAPSHOT_EXPIRY_DAYS`) is the backstop for everything it does
not know about — snapshots left by a control plane that has been replaced, or by
a cluster that no longer exists.

Keep the bucket rule strictly longer than what the k3s retention implies, or the
backstop starts deleting snapshots k3s still considers live.

---

## Upgrading the module pin

`~> 3.0` accepts 3.x and refuses 4.0. Widening it is not a version bump: read
upstream's `MIGRATION.md` first. Its v2→v3 notes rename inputs, invert booleans
and change nested shapes in a single tag — which is the reason nothing in this
kit references a module or chart without a version constraint.

Review `k3s_channel` at the same time. It is pinned to a minor channel rather
than `stable`, because `stable` is an unpinned dependency wearing a reassuring
name: it crosses minor boundaries on somebody else's schedule, and with
automatic upgrades on that happens without a diff in this repository.

## Two prerequisites that are easy to get wrong

Both were found by running this against a real project on 2026-08-04, not by
reading documentation.

### The OS snapshot must exist first, and nothing creates it

The module resolves its node image by **label selector**. A project without the
snapshot fails at plan time with:

```
Resource (image) was not found using label selector:
  leapmicro-snapshot=yes,kube-hetzner/os=leapmicro,kube-hetzner/k8s-distro=k3s
```

That reads like a permissions problem. It is not. Build it first, from the
template vendored in the module itself:

```bash
packer init  .terraform/modules/kube_hetzner/packer-template/hcloud-leapmicro-snapshots.pkr.hcl
packer build -only='hcloud.leapmicro-x86-snapshot'              .terraform/modules/kube_hetzner/packer-template/hcloud-leapmicro-snapshots.pkr.hcl
```

Build **only** the architecture you use. The template also contains an Arm
source, and at the time of writing no Arm instance type was purchasable in any
datacentre in this region — so building both fails on the half you do not need.
The x86 build takes about five minutes and destroys its own temporary server.

### Do NOT pre-register your SSH key in the project

This is counter-intuitive, and it stops the apply dead:

```
Error: SSH key not unique (uniqueness_error) — status 409
  with module.kube_hetzner.hcloud_ssh_key.k3s[0]
```

The module registers the key itself from `ssh_public_key`. Adding the same key
to the project by hand first — the obvious preparatory step — makes the module's
own registration a duplicate, and the provider rejects it.

Supply the key as a variable and let the module own it. You still hold the
private half, so node access is unaffected.

### Your project's resource quota may stop you before stock does

A Hetzner project has a cap on primary IP addresses, and a dual-stack node
consumes **two** — one IPv4, one IPv6. A five-node cluster therefore needs ten,
which a project that has not had its limits raised will refuse:

```
Error: Primary IP limit exceeded (resource_limit_exceeded)
```

Two things make this worse than a plain quota error. It arrives **mid-apply**,
after some nodes exist and some do not, so you are left converging a partial
cluster. And it is invisible beforehand — the API exposes no limit figure to
check against, so the only way to know is to hit it.

If you meet it: raise the project limit through support, or reduce the node
count. There is no per-nodepool switch to run nodes without public addresses
except on the autoscaler pool.

### The module refuses a two-node control plane, and it is right to

Setting the control-plane count to 2 fails validation outright. Two etcd members
are strictly worse than one: quorum is 2 of 2, so losing either stops the
cluster, whereas a single member at least fails only when it fails. One is
acceptable and non-HA; three is the smallest useful HA count.

This is worth knowing because 2 is exactly what someone reaches for when a
quota blocks 3 — and the module stops them. Reduce to 1 and accept non-HA, or
raise the quota. Do not go to 2.

### On Windows, set `core.autocrlf=false` before `tofu init` — or the cluster silently never starts

This is the single most expensive failure found while verifying this kit, and
nothing reports it.

OpenTofu downloads the node module **via git**. Git for Windows defaults to
`core.autocrlf=true`, which rewrites text files to CRLF on checkout. One of the
module's templates is an SELinux policy source, and it is embedded verbatim into
cloud-init and compiled **on the node**:

```
/root/kube_hetzner_selinux.te:1: ERROR 'unrecognized character' at token '0xd' on line 1
checkmodule: error(s) encountered while parsing configuration
semodule:  Failed on /root/kube_hetzner_selinux.pp!
Job for k8s-selinux-policy.service failed
```

`0xd` is a carriage return.

**What makes this expensive is everything that still succeeds.** Every server is
created. The provider reports them running. SSH works. `apply` reports only
opaque `remote-exec provisioner error` lines with `Process exited with status 2`.
The actual cause is three layers down in a failed systemd unit, and the visible
consequence is simply that **k3s never starts** — `systemctl is-system-running`
returns `degraded` and `systemctl is-active k3s` returns `inactive`.

Nothing in the chain mentions line endings.

Fix, without changing your global configuration:

```bash
printf '[core]
	autocrlf = false
	eol = lf
' > /tmp/gitconfig-lf
export GIT_CONFIG_GLOBAL=/tmp/gitconfig-lf
rm -rf .terraform/modules      # the bad copy is already on disk
tofu init
```

Verify before applying — the template must not report CRLF:

```bash
file .terraform/modules/kube_hetzner/templates/*.te
```

**Fixing it requires destroying the nodes, not re-applying.** The module keeps
`user_data` in a `lifecycle.ignore_changes` block — deliberately, so that editing
a template does not silently rebuild a running cluster. The consequence is that
correcting the line endings and running `apply` again changes *nothing* on
existing nodes: they keep the cloud-init they were born with. You must
`tofu destroy` and rebuild, or replace each node individually.

So the cost of getting this wrong is not a re-run. It is every node in the
cluster.

This is the same class of defect as the `.gitattributes` in this repository,
which pins `eol=lf` so a checkout platform cannot decide for you. That protects
*this* repository's files. It does nothing for a dependency git fetches on your
behalf — and that is where it bit.
