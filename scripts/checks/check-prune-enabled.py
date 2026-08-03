#!/usr/bin/env python3
"""LESSON: an apply-only pipeline never removes anything.

Resources deleted from the repository keep running indefinitely, and a resource
nobody remembers is a resource nobody patches. The ones that survive deletion
are disproportionately the ones somebody deliberately tried to remove.

Pruning is most of the reason this delivery mechanism was chosen, and Flux
defaults `spec.prune` to false -- so a reconciliation that simply omits the
field is an apply-only pipeline wearing a GitOps label. Omission and an
explicit `prune: false` are the same configuration and are treated the same
here.

Disabling it is sometimes right. What is never right is disabling it silently:
a workaround outlives its cause, and the comment explaining why is exactly what
lets a future reader tell a deliberate exception from a forgotten one. This
check accepts any prune-disabling that carries a written reason on, or directly
above, the line.

SEVERITY: boundary. Not because pruning is a security control, but because its
absence is invisible -- there is no error, no event and no drift report when a
deleted resource simply keeps running.
"""

import os
import re
import sys

sys.dont_write_bytecode = True  # no __pycache__ in a repository that does not ignore it
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

LESSON = "pruning off without a written reason is an apply-only pipeline nobody chose"

PRUNE_FALSE_RE = re.compile(r"^\s*prune:\s*(false|no|off)\s*(#.*)?$", re.IGNORECASE)
MIN_REASON = 20
EXEMPTION_RE = re.compile(
    r"#.*prune.*(because|reason|deliberate|intentional|on purpose)", re.IGNORECASE
)


def recorded_reason(lines, index):
    """A reason on the line itself, or in the comment block directly above it."""
    inline = lines[index].split("#", 1)
    if len(inline) == 2 and len(inline[1].strip()) >= MIN_REASON:
        return inline[1].strip()
    cursor = index - 1
    collected = []
    while cursor >= 0:
        stripped = lines[cursor].strip()
        if not stripped.startswith("#"):
            break
        collected.insert(0, stripped.lstrip("# ").strip())
        cursor -= 1
    joined = " ".join(collected).strip()
    return joined if len(joined) >= MIN_REASON else None


def body(check):
    docs = list(c.yaml_documents(check.root))
    reconciliations = [
        (parsed, doc)
        for parsed, doc in docs
        if c.kind_of(doc) == "Kustomization"
        and (c.api_of(doc) or "").startswith("kustomize.toolkit.fluxcd.io")
    ]
    if not reconciliations:
        check.skip("no Flux Kustomizations found yet")
        return

    # Explicit `prune: false` -- located by line, so the reason can be read.
    seen_files = set()
    for parsed, _ in reconciliations:
        if parsed.path in seen_files:
            continue
        seen_files.add(parsed.path)
        lines = parsed.text.splitlines()
        for index, line in enumerate(lines):
            if not PRUNE_FALSE_RE.match(line):
                continue
            reason = recorded_reason(lines, index)
            if reason:
                check.note(
                    "%s:%d prune disabled with a recorded reason: %s"
                    % (c.rel(check.root, parsed.path), index + 1, reason[:80])
                )
            else:
                check.boundary(
                    "prune is disabled with no recorded reason; resources deleted from git "
                    "keep running here forever",
                    parsed.path,
                    index + 1,
                )

    # Omission is the same configuration as false, and easier to miss.
    for parsed, doc in reconciliations:
        spec = doc.get("spec")
        if not isinstance(spec, dict) or "prune" in spec:
            continue
        if EXEMPTION_RE.search(parsed.text):
            check.note(
                "%s: prune omitted, but a reason is recorded in the file"
                % c.rel(check.root, parsed.path)
            )
            continue
        check.boundary(
            "Kustomization %r omits spec.prune, and Flux defaults it to false -- nothing "
            "deleted from git is ever removed from the cluster"
            % (c.name_of(doc) or "<unnamed>"),
            parsed.path,
            parsed.line_of("interval") or parsed.line_of("spec"),
        )

    check.note("inspected %d reconciliation(s)" % len(reconciliations))


if __name__ == "__main__":
    c.main("prune-enabled", LESSON, __doc__, body)
