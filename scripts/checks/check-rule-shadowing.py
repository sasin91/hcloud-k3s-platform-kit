#!/usr/bin/env python3
"""LESSON: an alerting rule can be committed, applied, and still never load.

Where one operator converts another project's rule kind into its own, two
objects of the same name in the same namespace are one object: the converter
overwrites whatever was applied there, and the loser leaves no trace. The rule
is in git, the apply succeeded, the object exists -- and the alert it defines
is not being evaluated by anything.

This was measured: a set of alert rules that everyone believed were running had
been silently replaced by a same-named object of the converted kind, and the
gap was invisible from the repository. Verifying rules against the *running*
evaluator belongs to the in-cluster checks; refusing to commit the collision in
the first place belongs here.

SEVERITY: boundary. A monitoring gap is silent by construction: the absence of
an alert looks exactly like the absence of a problem.
"""

import os
import sys

sys.dont_write_bytecode = True  # no __pycache__ in a repository that does not ignore it
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

LESSON = "a same-named rule of a converted kind silently replaces the one you applied"

# kind -> the kind an operator converts it into (or from)
CONVERTED_PAIRS = (
    ("PrometheusRule", "VMRule"),
    ("ServiceMonitor", "VMServiceScrape"),
    ("PodMonitor", "VMPodScrape"),
    ("Probe", "VMProbe"),
)


def body(check):
    docs = list(c.yaml_documents(check.root))
    index = {}
    for parsed, doc in docs:
        kind = c.kind_of(doc)
        name = c.name_of(doc)
        if not kind or not name:
            continue
        namespace = c.get(doc, "metadata", "namespace") or "<none>"
        index.setdefault((kind, namespace, name), []).append(parsed)

    relevant = [k for k in index if k[0] in [x for pair in CONVERTED_PAIRS for x in pair]]
    if not relevant:
        check.skip("no monitoring rule or scrape objects found yet")
        return

    for source_kind, target_kind in CONVERTED_PAIRS:
        for (kind, namespace, name), files in index.items():
            if kind != source_kind:
                continue
            collision = index.get((target_kind, namespace, name))
            if collision:
                check.boundary(
                    "%s/%s in namespace %s has a same-named %s; the operator's converter "
                    "overwrites one with the other and the loser is silently not evaluated"
                    % (source_kind, name, namespace, target_kind),
                    files[0].path,
                    files[0].line_of("name"),
                )

    for (kind, namespace, name), files in index.items():
        if len(files) > 1 and kind in [x for pair in CONVERTED_PAIRS for x in pair]:
            check.report(
                "%s/%s in namespace %s is declared in %d files; the last applied wins"
                % (kind, name, namespace, len(files)),
                files[0].path,
                None,
            )

    check.note("inspected %d rule or scrape object(s)" % len(relevant))


if __name__ == "__main__":
    c.main("rule-shadowing", LESSON, __doc__, body)
