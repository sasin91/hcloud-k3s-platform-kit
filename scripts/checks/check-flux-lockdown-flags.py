#!/usr/bin/env python3
"""LESSON: a reconciler's default identity is usually the most privileged one.

Where a reconciliation resource does not name a service account, it is applied
by the controller's own identity -- and upstream binds these controllers to
cluster administrator. Anyone able to create such a resource in any namespace
therefore acts as cluster administrator, regardless of their own permissions. A
namespace does not contain a controller acting on that namespace's behalf: the
boundary is the identity applying the manifests, not where they land.

The multi-tenancy lockdown flags close this, and none of them is on by default:

    --no-cross-namespace-refs=true   tenants cannot reference Flux resources elsewhere
    --no-remote-bases=true           bases must be local files, not remote URLs
    --default-service-account=...    an unspecified reconciliation gets a powerless identity

WHY THIS CHECK BUILDS THE OVERLAY INSTEAD OF READING THE PATCH FILES.

Two kustomize behaviours, both verified locally on 2026-08-03, make a text-
reading version of this check worthless:

  * a patch whose target matches no resource is applied to nothing and reports
    nothing -- a typo in a controller name removes a privilege boundary
    silently; and
  * a multi-document JSON 6902 patch file applies only its FIRST document and
    discards the rest without an error.

Either one produces a repository whose patch files contain all three flags and
a cluster that receives none of them. So the check renders the overlay and
reads the container arguments of the built Deployments. That is the only form
of this check that cannot be satisfied by a file that does nothing -- which is
exactly the failure this kit exists to document: a check that looks correct,
passes, and verifies nothing.

WHY THE FLAGS DIFFER PER CONTROLLER. They are not uniformly accepted:
kustomize-controller defines --no-remote-bases and helm-controller does not,
and a controller started with an unknown flag exits. Requiring all three
everywhere would produce a configuration that satisfies this check and
crash-loops helm-controller. The matrix below is from upstream's own main.go
files.

SEVERITY: boundary, every flag in the matrix, no exceptions. The failure mode
is privilege escalation to cluster administrator by anyone who can create a
YAML file in any namespace.
"""

import os
import re
import sys

sys.dont_write_bytecode = True  # no __pycache__ in a repository that does not ignore it
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

LESSON = "without the lockdown flags a tenant reconciliation runs as cluster administrator"

CROSS_NS = "--no-cross-namespace-refs"
REMOTE_BASES = "--no-remote-bases"
DEFAULT_SA = "--default-service-account"

# controller -> flags that controller both accepts and must carry
REQUIRED = {
    "kustomize-controller": (CROSS_NS, REMOTE_BASES, DEFAULT_SA),
    # helm-controller does NOT define --no-remote-bases; passing it exits the
    # process. It takes the other two.
    "helm-controller": (CROSS_NS, DEFAULT_SA),
    # Resolves cross-namespace references to providers and alerts, so it takes
    # the first flag and no others.
    "notification-controller": (CROSS_NS,),
    # image-automation-controller accepts --no-cross-namespace-refs where it is
    # installed. It is not part of the kit's default install.
    "image-automation-controller": (CROSS_NS,),
}

NO_FLAGS = ("source-controller", "image-reflector-controller")

FLAG_RE = re.compile(r"(--[a-z0-9-]+)(?:=(\S+))?")


def flux_overlay_dirs(root):
    """Directories whose kustomization builds the controllers."""
    found = []
    for path in c.iter_files(root, ("kustomization.yaml", "kustomization.yml")):
        directory = os.path.dirname(path)
        text = c.read_text(path)
        if "gotk-components" in text or "flux-system" in os.path.basename(directory):
            found.append(directory)
    return sorted(set(found))


def flags_from_built(rendered):
    """controller -> {flag: value} read from built Deployment container args."""
    flags = {}
    parsed = c.YamlFile("<built>", rendered)
    for doc in parsed.docs:
        if not isinstance(doc, dict) or c.kind_of(doc) != "Deployment":
            continue
        name = c.name_of(doc)
        if name not in REQUIRED and name not in NO_FLAGS:
            continue
        found = {}
        containers = c.get(doc, "spec", "template", "spec", "containers") or []
        for container in containers:
            if not isinstance(container, dict):
                continue
            for arg in container.get("args") or []:
                match = FLAG_RE.match(str(arg))
                if match and match.group(1) in (CROSS_NS, REMOTE_BASES, DEFAULT_SA):
                    found[match.group(1)] = match.group(2)
        flags[name] = found
    return flags


def flags_from_text(root):
    """Fallback when no build tool exists. Weaker on purpose -- see the header."""
    flags = {}
    seen = set()
    controllers = tuple(REQUIRED) + NO_FLAGS

    def record(controller, text):
        for match in FLAG_RE.finditer(text):
            if match.group(1) in (CROSS_NS, REMOTE_BASES, DEFAULT_SA):
                flags.setdefault(controller, {})[match.group(1)] = match.group(2)

    for path in c.yaml_files(root):
        text = c.read_text(path)
        for controller in controllers:
            if controller in text:
                seen.add(controller)
                record(controller, text)
    return flags, seen


def body(check):
    directories = flux_overlay_dirs(check.root)
    if not directories:
        check.skip("no Flux controller overlay found yet")
        return

    tool, _ = c.kustomize_tool()
    built = {}
    if tool:
        for directory in directories:
            rendered, error = c.kustomize_build(directory)
            if error:
                check.boundary(
                    "cannot build %s, so the lockdown flags cannot be verified: %s"
                    % (c.rel(check.root, directory), error),
                    directory,
                )
                continue
            for controller, flags in flags_from_built(rendered).items():
                built.setdefault(controller, {}).update(flags)
        check.note("verified against built output from %s" % tool)
    else:
        built, _ = flags_from_text(check.root)
        check.report(
            "neither kustomize nor kubectl is installed, so the flags were read from patch "
            "text rather than built output. A patch whose target matches nothing, and every "
            "document after the first in a JSON 6902 patch file, are silently ignored -- "
            "this weaker form of the check cannot see either.",
            None,
            None,
        )

    if not built:
        # Distinguish "the controllers are not committed yet" from "they are
        # committed and the build lost them". The first is a repository that is
        # not finished; the second is the silent-patch failure this check
        # exists for, and it must not pass.
        # Look for an actual Deployment document, not the words: a patch target
        # naming a controller is not a controller.
        committed = any(
            c.kind_of(doc) == "Deployment" and c.name_of(doc) in REQUIRED
            for _, doc in c.yaml_documents(check.root)
        )
        if committed:
            check.boundary(
                "controller Deployments are committed but none survived the build of %s; the "
                "lockdown flags are unverified" % c.rel(check.root, directories[0]),
                directories[0],
            )
        else:
            check.skip(
                "the controller manifests are not committed yet (the overlay renders no "
                "controller Deployments), so there are no arguments to verify"
            )
        return

    for controller, required in REQUIRED.items():
        present = built.get(controller)
        if present is None:
            continue  # controller not installed in this fork
        for flag in required:
            if flag not in present:
                check.boundary(
                    "%s is missing %s -- without it a reconciliation that names no service "
                    "account is applied with cluster-admin" % (controller, flag),
                    None,
                    None,
                )
                continue
            value = present[flag]
            if flag == DEFAULT_SA:
                if not value:
                    check.boundary("%s sets %s with no value" % (controller, flag))
            elif value is not None and str(value).lower() not in ("true", "1"):
                check.boundary(
                    "%s sets %s=%s, which disables the lockdown it exists to provide"
                    % (controller, flag, value)
                )

    # A flag a controller does not define makes it exit on start.
    for controller in NO_FLAGS:
        for flag in built.get(controller, {}):
            check.boundary(
                "%s carries %s, which that binary does not define; it will exit on start"
                % (controller, flag)
            )
    if REMOTE_BASES in built.get("helm-controller", {}):
        check.boundary(
            "helm-controller carries %s, which it does not define; it will crash-loop"
            % REMOTE_BASES
        )

    check.note("inspected %d controller(s): %s" % (len(built), ", ".join(sorted(built))))


if __name__ == "__main__":
    c.main("flux-lockdown-flags", LESSON, __doc__, body)
