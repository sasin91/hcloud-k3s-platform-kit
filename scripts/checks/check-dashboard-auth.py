#!/usr/bin/env python3
"""LESSON: two layers where the inner one is disabled is one layer in a costume.

An observability dashboard is a query interface, not a set of charts: explore
runs arbitrary queries against every datasource, logs carry request paths,
client addresses and credentials somebody logged by mistake, traces are the
architecture diagram nobody wrote down, and there is usually no per-tenant
isolation inside the stack at all. One credential is every tenant at once.

The kit does not expose it -- and hardens its authentication anyway, because
"we didn't expose it" is a claim about today. Whoever adds a route next year
inherits whatever the defaults were. A real cluster ran anonymous access at
Editor privilege behind one forward-auth middleware while the comment guarding
it said the inner role was read-only. The comment was wrong by a privilege
level, and no check existed that would have caught it.

SEVERITY:
  boundary -- anonymous access enabled; administrative credentials absent,
              empty, or a published default. These are the privilege boundary.
  config   -- anonymous access merely left at the chart default rather than
              disabled explicitly. Prose describing a posture drifts; so does
              an implicit default.
"""

import os
import re
import sys

sys.dont_write_bytecode = True  # no __pycache__ in a repository that does not ignore it
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

LESSON = "harden the inner layer while the outer one still works, not afterwards"

KNOWN_DEFAULT_PASSWORDS = {
    "admin",
    "prom-operator",
    "password",
    "changeme",
    "grafana",
    "secret",
    "admin123",
}

ADMIN_KEYS = (
    "adminPassword",
    "admin_password",
    "adminPasswordKey",
    "existingSecret",
    "admin",
    "GF_SECURITY_ADMIN_PASSWORD",
)

ENCRYPTED_MARKERS = ("ENC[", "sops:", "valueFrom", "existingSecret", "secretKeyRef")


def anonymous_setting(node):
    """(enabled, where) for anonymous access, or (None, None) if unconfigured."""
    for dotted, value in c.walk(node):
        key = dotted[-1] if dotted else ""
        joined = ".".join(dotted).lower()
        if key in ("GF_AUTH_ANONYMOUS_ENABLED",) or joined.endswith("gf_auth_anonymous_enabled"):
            return str(value).lower() in ("true", "1", "yes"), ".".join(dotted)
        if "auth.anonymous" in joined or joined.endswith("auth.anonymous.enabled"):
            if isinstance(value, dict) and "enabled" in value:
                return bool(value["enabled"]), ".".join(dotted) + ".enabled"
            if key == "enabled":
                return bool(value), ".".join(dotted)
        if isinstance(value, dict) and set(value) >= {"anonymous"} and key == "auth":
            inner = value.get("anonymous")
            if isinstance(inner, dict) and "enabled" in inner:
                return bool(inner["enabled"]), ".".join(dotted) + ".anonymous.enabled"
    # ini shipped as a block of text
    for _, value in c.walk(node):
        if isinstance(value, str) and "[auth.anonymous]" in value:
            match = re.search(r"\[auth\.anonymous\](?:[^\[]*?)enabled\s*=\s*(\w+)", value, re.S)
            if match:
                return match.group(1).lower() in ("true", "1"), "grafana.ini text"
            return True, "grafana.ini text"  # section present, enabled defaults on in that form
    return None, None


def admin_credentials(node):
    """(state, detail) where state is one of: missing, reference, literal."""
    for dotted, value in c.walk(node):
        key = dotted[-1] if dotted else ""
        joined = ".".join(dotted)
        if key not in ADMIN_KEYS and "admin_password" not in joined.lower():
            continue
        if isinstance(value, dict):
            if value.get("existingSecret") or value.get("passwordKey"):
                return "reference", joined
            continue
        if value is None or value == "":
            return "empty", joined
        text = str(value)
        if any(marker in text for marker in ENCRYPTED_MARKERS):
            return "reference", joined
        if key == "existingSecret":
            return "reference", joined
        if text.lower() in KNOWN_DEFAULT_PASSWORDS:
            return "default", "%s = %s" % (joined, text)
        return "literal", joined
    return "missing", None


def body(check):
    docs = list(c.yaml_documents(check.root))
    scopes = list(c.dashboard_scopes(docs))
    if not scopes:
        check.skip("no dashboard configuration found yet")
        return

    for parsed, label, node in scopes:
        # AN ENCRYPTED SECRET IS NOT AN ABSENT ONE. A fork that follows the
        # instructions commits the admin credential SOPS-encrypted, so every
        # field this check looks for is ciphertext. Reading that as "no
        # credential configured" fails the guardrail for doing exactly the
        # right thing -- and the only way to make it pass would be to commit
        # the password in clear.
        #
        # What can still be checked is that the release REFERENCES a secret
        # rather than relying on a chart default, and that is checked where the
        # release is declared. Here, say plainly that the contents are
        # unreadable rather than inventing a verdict about them.
        raw = c.read_text(parsed.path)
        if "ENC[" in raw and "sops:" in raw:
            check.note(
                "%s: SOPS-encrypted, so its contents cannot be inspected here. The "
                "reference to it is verified where the release is declared" % label
            )
            continue

        enabled, where = anonymous_setting(node)
        if enabled is True:
            check.boundary(
                "%s: anonymous access is enabled (%s) -- read access to the dashboard is read "
                "access to everything the platform has recorded" % (label, where),
                parsed.path,
                parsed.line_of("enabled"),
            )
        elif enabled is None:
            check.report(
                "%s: anonymous access is not explicitly disabled; it rests on a chart default "
                "that whoever adds a route later will inherit" % label,
                parsed.path,
                parsed.line_of("grafana.ini") or parsed.line_of("spec"),
            )

        state, detail = admin_credentials(node)
        if state == "missing":
            check.boundary(
                "%s: no administrative credential is configured, so the chart's published "
                "default password applies" % label,
                parsed.path,
                parsed.line_of("spec"),
            )
        elif state == "empty":
            check.boundary(
                "%s: administrative credential %s is empty" % (label, detail),
                parsed.path,
                parsed.line_of("adminPassword"),
            )
        elif state == "default":
            check.boundary(
                "%s: administrative credential is a known default (%s)" % (label, detail),
                parsed.path,
                parsed.line_of("adminPassword"),
            )
        elif state == "literal":
            check.boundary(
                "%s: administrative credential %s is a plaintext literal in git; it belongs in "
                "an encrypted secret" % (label, detail),
                parsed.path,
                parsed.line_of("adminPassword"),
            )

    check.note("inspected %d dashboard configuration scope(s)" % len(scopes))


if __name__ == "__main__":
    c.main("dashboard-auth", LESSON, __doc__, body)
