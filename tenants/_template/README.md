# The tenant template

Copy this directory, replace the tokens, add the copy to the cluster's tenants stage. That is the whole procedure, and the three steps are in that order for a reason: the third is what makes the first two real.

This template **is** the kit's demonstration of multi-tenancy. No example tenant ships, because a workload occupying a namespace shows nothing these files do not already state.

---

## Adding tenant number two

```bash
# 1. Copy. The directory name is the tenant name.
cp -r tenants/_template tenants/acme
rm tenants/acme/README.md

# 2. Replace the tokens. Three of them.
grep -rl 'REPLACE-tenant' tenants/acme | xargs sed -i 's/REPLACE-tenant/acme/g'
grep -rl 'REPLACE-domains' tenants/acme | xargs sed -i 's/REPLACE-domains/acme.example.com/g'
grep -rl 'REPLACE-repository-url' tenants/acme \
  | xargs sed -i 's|REPLACE-repository-url|ssh://git@example.com/org/repo.git|g'

# 3. Nothing should be left.
grep -rn 'REPLACE-' tenants/acme

# 4. Declare that the tenant exists.
#    Add `- acme` to the resources list in tenants/kustomization.yaml.
```

Step 3 is a guardrail candidate rather than a courtesy: **fail the pull request if any file under `tenants/` outside `_template/` still contains `REPLACE-`.** A half-replaced template applies cleanly — the values are valid strings — and produces a tenant whose backup host, authorised domains or reconciliation path belong to a tenant that does not exist.

Step 4 is the one that makes the other three real, and it is also what keeps this template out of the cluster: the tenants stage applies `./tenants`, and without an explicit resource list Flux would scan the directory and apply `_template/` as a tenant called `tenant-REPLACE-tenant`.

That stage applies `tenants/acme` **as the platform**, not as the tenant. It has to: it creates a namespace and a role binding. The reconciliation it in turn creates — `sync.yaml`, inside the tenant's namespace — is the one that names the tenant's service account. A tenant cannot be trusted to apply its own quota, and does not need to be trusted to apply its own workloads.

### The tokens

| Token | Becomes | Appears in |
|---|---|---|
| `REPLACE-tenant` | the tenant name; namespace is `tenant-<name>` | every file — namespace, service account, role, restic `--host`, sync path |
| `REPLACE-domains` | comma-separated hostnames, no whitespace | `namespace.yaml` annotation, read by the admission policy |
| `REPLACE-repository-url` | the git URL Flux reads the tenant's workloads from | `sync.yaml` |

Everything else has a working default and is meant to be edited with intent: quota sizes, limit range defaults, retention, the backup schedule and the snapshot floor.

### Then, per tenant, by hand

Two secrets, because they cannot be generated:

- `backup-secret.example.yaml` → `backup-secret.yaml`, SOPS-encrypted, added to `kustomization.yaml`. One restic repository per namespace.
- `deploy-key-secret.example.yaml` → `deploy-key-secret.yaml`, same treatment. Delete both it and the `secretRef` in `sync.yaml` if the repository is public.

And one edit that decides whether the backup is real: point `volumes[data].persistentVolumeClaim.claimName` at the tenant's actual volume, or switch to the database-dump path commented at the bottom of the script. **Until that is done the job cannot succeed** — the claim does not exist, the pod stays Pending, the dead-man's-switch alert fires. That is deliberate. A job that succeeds against an empty directory is the exact failure the assertions in it exist to prevent.

---

## What each file is for

| File | Guarantee |
|---|---|
| `namespace.yaml` | the managed label, the pod security level, the authorised domain list |
| `resourcequota.yaml` | aggregate requests, limits, storage, object counts, storage class assignment |
| `limitrange.yaml` | per-container defaults — without it the quota is aspirational |
| `networkpolicy.yaml` | default-deny both directions, with the allowances stated one per policy |
| `poddisruptionbudget.yaml` | survives a node drain without deadlocking one |
| `rbac.yaml` | the identity, and the boundary that identity cannot cross |
| `backup-cronjob.yaml` | restic, asserting on its output, with a stable `--host` |
| `sync.yaml` | the tenant's own reconciliation, naming that identity |
| `workloads/` | the only path the tenant's identity reconciles |

Each file carries its reasoning at the line it constrains. Reading them in that order is the fastest description of the tenancy model in the repository.

---

## Copy-and-edit, and what it costs

This is a template that is copied, not a base that is shared. A shared base would propagate later improvements into existing tenants; copies do not.

That is a real cost and it is accepted on purpose, because it makes the kit's own lesson unavoidable:

> Generation is prospective. A generator covers everything created after it learned to, and never goes back.

A shared base would soften that for guarantees expressible as a base — and change nothing about tenants provisioned before the base existed, or created by hand, or edited afterwards. The compensator is the same either way: a scheduled check that reports tenants missing a guarantee. See `tenants/README.md`.

A fork that prefers propagation can convert this directory into a kustomize base and reduce each tenant to a kustomization with patches. Most of the per-tenant values — quota sizes, retention, the backup source, the domain list — end up patched individually, so the saving is smaller than it looks.

---

**Not verified against a running cluster.** Nothing in this directory has been applied to one. The versions are pinned and the manifests are internally consistent; that is a different claim from working.
