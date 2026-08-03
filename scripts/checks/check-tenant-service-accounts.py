#!/usr/bin/env python3
"""LESSON: the boundary is the identity applying the manifests, not where they land.

A reconciliation that names no service account is applied by the controller's
own identity, and that identity is bound to cluster administrator. A namespace
per tenant is then a naming convention rather than a privilege boundary: the
tenant's manifests land in their namespace, but they were applied by an account
that could have put them anywhere.

Naming an explicit service account -- with a namespace-scoped role binding
generated alongside the tenant -- is what turns the convention into a boundary.

SEVERITY:
  boundary -- a tenant reconciliation with no service account. Always.
  boundary -- any reconciliation with no service account while
              --default-service-account is not configured anywhere, because
              then the fallback is cluster administrator rather than a
              powerless account.
  config   -- a platform reconciliation with no service account while the
              default-service-account lockdown *is* in place: it will reconcile
              as a powerless identity, which is a broken rollout rather than an
              open door.
"""

import os
import sys

sys.dont_write_bytecode = True  # no __pycache__ in a repository that does not ignore it
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

LESSON = "every reconciliation names the identity it applies with, or inherits cluster-admin"

FLUX_RECONCILIATION_KINDS = ("Kustomization", "HelmRelease")


def is_flux_reconciliation(doc):
    api = c.api_of(doc) or ""
    kind = c.kind_of(doc)
    if kind == "Kustomization":
        return api.startswith("kustomize.toolkit.fluxcd.io")
    if kind == "HelmRelease":
        return api.startswith("helm.toolkit.fluxcd.io")
    return False


def is_tenant_reconciliation(root, parsed, doc):
    location = c.rel(root, parsed.path).lower()
    if "tenant" in location:
        return True
    spec_path = c.get(doc, "spec", "path")
    if isinstance(spec_path, str) and "tenant" in spec_path.lower():
        return True
    name = (c.name_of(doc) or "").lower()
    if "tenant" in name:
        return True
    return False


def default_sa_lockdown_present(root):
    for path in c.yaml_files(root):
        if "--default-service-account" in c.read_text(path):
            return True
    return False


def body(check):
    docs = list(c.yaml_documents(check.root))
    reconciliations = [(p, d) for p, d in docs if is_flux_reconciliation(d)]
    if not reconciliations:
        check.skip("no Flux reconciliations found yet")
        return

    lockdown = default_sa_lockdown_present(check.root)
    if not lockdown:
        check.note(
            "--default-service-account is not configured anywhere, so an unspecified "
            "reconciliation falls back to the controller identity"
        )

    declared_accounts = set()
    for _, doc in docs:
        if c.kind_of(doc) == "ServiceAccount":
            declared_accounts.add(c.name_of(doc))

    for parsed, doc in reconciliations:
        name = c.name_of(doc) or "<unnamed>"
        kind = c.kind_of(doc)
        account = c.get(doc, "spec", "serviceAccountName")
        tenant = is_tenant_reconciliation(check.root, parsed, doc)

        if not account:
            if tenant:
                check.boundary(
                    "tenant %s %r names no serviceAccountName; it is applied by the "
                    "controller's own cluster-admin identity" % (kind, name),
                    parsed.path,
                    parsed.line_of("spec"),
                )
            elif not lockdown:
                check.boundary(
                    "%s %r names no serviceAccountName and no --default-service-account "
                    "lockdown exists; it reconciles as cluster administrator" % (kind, name),
                    parsed.path,
                    parsed.line_of("spec"),
                )
            else:
                check.report(
                    "platform %s %r names no serviceAccountName; the lockdown default applies "
                    "and it will reconcile with a powerless identity" % (kind, name),
                    parsed.path,
                    parsed.line_of("spec"),
                )
            continue

        # Only tenant accounts are expected to be declared in this repository:
        # the controllers' own service accounts come from the generated
        # upstream manifests, so demanding to see them here would be a
        # permanent false positive -- and a check that cries wolf gets muted.
        if tenant and declared_accounts and account not in declared_accounts:
            check.report(
                "%s %r names serviceAccountName %r, which no ServiceAccount in this repository "
                "declares" % (kind, name, account),
                parsed.path,
                parsed.line_of("serviceAccountName"),
            )

    check.note("inspected %d reconciliation(s)" % len(reconciliations))


if __name__ == "__main__":
    c.main("tenant-service-accounts", LESSON, __doc__, body)
