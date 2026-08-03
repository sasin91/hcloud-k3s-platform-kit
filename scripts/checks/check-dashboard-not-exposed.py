#!/usr/bin/env python3
"""LESSON: the safest configuration is the one that does not exist.

Every alternative to this check is a way of *defending* an internet-facing
observability dashboard -- hardened built-in auth, an ingress credential, a
proxy against an external identity provider. None is as safe as not having a
route. The kit therefore ships no Ingress, no IngressRoute, no HTTPRoute, no
LoadBalancer Service and no hostname for the dashboard; the documented access
path is a port-forward, and exposing it is a deliberate act with its own
documentation rather than a default somebody inherits without noticing.

This check exists because that default is one `enabled: true` away from being
undone, in a values file nobody rereads.

SEVERITY: boundary. A route to the dashboard is unauthenticated read access to
every log line, trace and metric the platform has ever recorded, for every
tenant at once. Commented-out examples are invisible to this check, which is
correct: a commented route routes nothing.
"""

import os
import sys

sys.dont_write_bytecode = True  # no __pycache__ in a repository that does not ignore it
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

LESSON = "ship no route to the dashboard; make exposure an explicit, documented act"

ROUTE_KINDS = (
    "Ingress",
    "IngressRoute",
    "IngressRouteTCP",
    "HTTPRoute",
    "TLSRoute",
    "GRPCRoute",
    "TCPRoute",
)

EXPOSED_SERVICE_TYPES = ("LoadBalancer", "NodePort")

ROUTE_VALUE_KEYS = ("ingress", "route", "httpRoute", "ingressRoute", "gatewayRoute")


def refers_to_dashboard(doc):
    for value in c.strings_in(doc):
        if any(name in value.lower() for name in c.DASHBOARD_NAMES):
            return True
    name = (c.name_of(doc) or "").lower()
    return any(dashboard in name for dashboard in c.DASHBOARD_NAMES)


def body(check):
    docs = list(c.yaml_documents(check.root))
    if not docs:
        check.skip("no manifests in the repository yet")
        return

    found_dashboard = False

    for parsed, doc in docs:
        kind = c.kind_of(doc)
        if kind in ROUTE_KINDS and refers_to_dashboard(doc):
            found_dashboard = True
            check.boundary(
                "%s %s routes to the dashboard; the kit exposes it by port-forward only"
                % (kind, c.name_of(doc) or "<unnamed>"),
                parsed.path,
                parsed.line_of("kind"),
            )
        if kind == "Service" and refers_to_dashboard(doc):
            found_dashboard = True
            service_type = c.get(doc, "spec", "type")
            if service_type in EXPOSED_SERVICE_TYPES:
                check.boundary(
                    "Service %s for the dashboard is type %s, which is a route by another name"
                    % (c.name_of(doc) or "<unnamed>", service_type),
                    parsed.path,
                    parsed.line_of("type"),
                )

    for parsed, label, node in c.dashboard_scopes(docs):
        found_dashboard = True
        for dotted, value in c.find_values(node, ROUTE_VALUE_KEYS):
            if isinstance(value, dict) and value.get("enabled") is True:
                check.boundary(
                    "%s enables %s -- that publishes the dashboard" % (label, dotted),
                    parsed.path,
                    parsed.line_of("enabled"),
                )
        for dotted, value in c.find_values(node, ("hostname", "host", "hosts", "domain")):
            if value:
                check.report(
                    "%s declares %s = %r; a hostname is usually the first half of a route"
                    % (label, dotted, value),
                    parsed.path,
                    c.find_line(parsed.text, str(value)),
                )

    if not found_dashboard:
        check.skip("no dashboard configuration found yet")
    else:
        check.note("no route to the dashboard is declared")


if __name__ == "__main__":
    c.main("dashboard-not-exposed", LESSON, __doc__, body)
