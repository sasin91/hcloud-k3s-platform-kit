#!/usr/bin/env python3
"""LESSON: two certificate issuers in one cluster is the race, reintroduced.

An ingress controller's built-in ACME client stores certificate state locally,
so replicas race, re-issue against each other, and burn the account's issuance
limits. cert-manager issues into Secrets that any number of replicas read, and
the constraint disappears rather than being managed.

So an ACME resolver configured on the ingress *while cert-manager is present*
is not a redundant belt-and-braces setup. It means somebody reintroduced the
exact race cert-manager was installed to remove, and the ingress is now one
step away from being pinned back to a single replica to "fix" it.

SEVERITY: boundary. Certificate issuance is a shared, rate-limited, externally
visible resource; two issuers racing over it takes hostnames down for everyone
in the cluster, not just the tenant who added the annotation.
"""

import os
import re
import sys

sys.dont_write_bytecode = True  # no __pycache__ in a repository that does not ignore it
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

LESSON = "one certificate authority per cluster -- the ingress must not run its own"

CERT_MANAGER_KINDS = ("Issuer", "ClusterIssuer", "Certificate", "CertificateRequest", "Challenge", "Order")

# Keys whose PRESENCE means nothing and whose CONTENT means everything.
# `certificatesResolvers: {}` is the kit's way of saying "deliberately none",
# and a check that flagged it would be a check that punishes the explicit
# statement of the very property it is testing for.
RESOLVER_KEYS = ("certificatesresolvers", "certresolver", "certificateresolver")

# Command-line and annotation forms, where the string itself is the evidence.
ACME_STRINGS = (
    (r"--certificatesresolvers\.", "a Traefik certificate resolver flag is set"),
    (r"--acme\.", "an ACME command-line flag is set"),
    (r"certresolver=\S", "a certresolver is selected on a router"),
)

HCL_ACME_VARS = ("traefik_acme_tls", "traefik_acme_email", "nginx_acme_tls")


def cert_manager_present(check, docs):
    for parsed, doc in docs:
        if c.kind_of(doc) in CERT_MANAGER_KINDS:
            return "%s in %s" % (c.kind_of(doc), c.rel(check.root, parsed.path))
        chart = c.get(doc, "spec", "chart", "spec", "chart")
        if isinstance(chart, str) and "cert-manager" in chart:
            return "cert-manager HelmRelease in %s" % c.rel(check.root, parsed.path)
        name = (c.name_of(doc) or "").lower()
        if name.startswith("cert-manager") and c.kind_of(doc) in ("HelmRelease", "Kustomization"):
            return "cert-manager reconciliation in %s" % c.rel(check.root, parsed.path)
    for path in c.hcl_files(check.root):
        text = c.strip_hcl_comments(c.read_text(path))
        if re.search(r"cert[_-]manager", text):
            return "cert-manager referenced in %s" % c.rel(check.root, path)
    return None


def body(check):
    docs = list(c.yaml_documents(check.root))
    present = cert_manager_present(check, docs)
    if present is None:
        check.skip("cert-manager is not present; there is no second issuer to conflict with")
        return
    check.note("cert-manager present: %s" % present)

    hits = [0]

    def flag(description, parsed, hint):
        hits[0] += 1
        check.boundary(
            "%s while cert-manager is installed -- this is the issuance race cert-manager "
            "removes" % description,
            parsed.path,
            c.find_line(parsed.text, hint) if hint else None,
        )

    def scan(node, parsed, depth=0):
        for path_tuple, value in c.walk(node):
            key = path_tuple[-1].lower() if path_tuple else ""
            if any(resolver in key for resolver in RESOLVER_KEYS):
                # Empty means "deliberately none". Non-empty means an issuer.
                if value not in (None, "", {}, [], False):
                    flag(
                        "a certificate resolver is configured (%s = %r)"
                        % (".".join(path_tuple), value),
                        parsed,
                        path_tuple[-1],
                    )
            if isinstance(value, str):
                for pattern, description in ACME_STRINGS:
                    if re.search(pattern, value, re.IGNORECASE):
                        flag(description, parsed, value[:60])
                        break
                # Chart values shipped as a block of YAML inside a string.
                if depth == 0 and ("certificatesResolvers" in value or "certResolver" in value):
                    inner = c.YamlFile(parsed.path, value)
                    for doc in inner.docs:
                        if isinstance(doc, dict):
                            scan(doc, parsed, depth + 1)

    for path in c.yaml_files(check.root):
        parsed = c.load_yaml(path)
        for doc in parsed.docs:
            if not isinstance(doc, dict):
                continue
            # cert-manager's own objects are full of the word "acme" and are the
            # legitimate issuer. Only the ingress side is a conflict.
            if c.kind_of(doc) in CERT_MANAGER_KINDS:
                continue
            scan(doc, parsed)

    for path in c.hcl_files(check.root):
        text = c.strip_hcl_comments(c.read_text(path))
        for variable in HCL_ACME_VARS:
            value = c.hcl_attr(text, variable)
            if value is None:
                continue
            if variable.endswith("_tls") and str(value).lower() not in ("true", "1"):
                continue
            hits[0] += 1
            check.boundary(
                "%s = %s hands certificate issuance to the ingress while cert-manager is "
                "installed" % (variable, value),
                path,
                c.find_line(text, variable),
            )

    if hits[0] == 0:
        check.note("no ACME resolver configured on the ingress")


if __name__ == "__main__":
    c.main("acme-conflict", LESSON, __doc__, body)
