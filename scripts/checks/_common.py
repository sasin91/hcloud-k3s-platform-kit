#!/usr/bin/env python3
"""Shared helpers for the guardrail checks.

SEVERITY LIVES HERE, IN ONE PLACE, ON PURPOSE.

    BOUNDARY -- a security or capacity boundary. Exits non-zero. Fails the build.
    CONFIG   -- standing configuration. Prints a report. Does not fail the build.

    "Security boundaries fail. Standing configuration reports. An absent quota
     is a gap for somebody to close; an absent privilege boundary is a hole to
     stop. Conflating them trains people to ignore both -- and a wall of
     warnings is indistinguishable from no warnings at all."

Every finding printed by any check carries its class in the output, so a reader
of a build log can tell in one glance whether something must be fixed before
merge or scheduled for later.

WHY NO DEPENDENCIES: a fork must be able to run these with nothing installed
beyond what a stock CI runner and a stock developer machine already have. That
is why the YAML reader below is hand-rolled against the Python standard library
rather than importing PyYAML. It is deliberately partial -- it understands the
subset of YAML that Kubernetes manifests, Helm values and kustomize overlays
are written in -- and it fails soft: an unparsable file is reported, never a
crash, because these checks run against a repository other people are still
writing.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import traceback

BOUNDARY = "boundary"
CONFIG = "config"

SKIP_DIRS = {
    ".git",
    ".terraform",
    "node_modules",
    "vendor",
    "dist",
    ".venv",
    "venv",
    "__pycache__",
}

YAML_SUFFIXES = (".yaml", ".yml")
HCL_SUFFIXES = (".tf", ".tofu", ".tfvars")


# --------------------------------------------------------------------------
# Repository access
# --------------------------------------------------------------------------


def repo_root(explicit=None):
    """Root of the repository under check.

    Order: explicit --root, then $REPO_ROOT, then two levels up from this file
    (scripts/checks/_common.py -> repository root).
    """
    if explicit:
        return os.path.abspath(explicit)
    env = os.environ.get("REPO_ROOT")
    if env:
        return os.path.abspath(env)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, os.pardir, os.pardir))


def rel(root, path):
    """Path relative to the repository root, in posix form, for stable output."""
    try:
        r = os.path.relpath(path, root)
    except ValueError:
        r = path
    return r.replace(os.sep, "/")


def iter_files(root, suffixes, skip_dirs=SKIP_DIRS):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs)
        for name in sorted(filenames):
            if name.endswith(tuple(suffixes)):
                yield os.path.join(dirpath, name)


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def find_line(text, needle, regex=False):
    """Best-effort line number for a key or literal, for pointing at a finding.

    The YAML reader below returns plain dicts rather than a node tree, so line
    numbers for findings are recovered by searching the source. Approximate is
    fine: it only has to put a reader within a screen of the problem.
    """
    pattern = needle if regex else re.escape(needle)
    try:
        compiled = re.compile(pattern)
    except re.error:
        return None
    for number, line in enumerate(text.splitlines(), 1):
        if compiled.search(line):
            return number
    return None


# --------------------------------------------------------------------------
# Findings and exit codes
# --------------------------------------------------------------------------


class Check:
    """Collects findings, prints them with their severity, sets the exit code.

    Exit codes:
        0 -- passed, or skipped because the files it inspects do not exist yet,
             or produced only CONFIG reports.
        1 -- at least one BOUNDARY failure (or, with --strict, any finding).
        2 -- the check itself broke. A broken check is not a passing check.
    """

    def __init__(self, name, lesson, args=None):
        self.name = name
        self.lesson = lesson
        self.args = args
        self.strict = bool(getattr(args, "strict", False))
        self.root = repo_root(getattr(args, "root", None))
        self.failures = []
        self.reports = []
        self.skipped = None
        self.checked = 0
        self._emitted = set()  # the same finding twice is noise, and noise gets muted

    # -- recording ---------------------------------------------------------

    def _seen(self, message, path, line):
        key = (message, str(path), line)
        if key in self._emitted:
            return True
        self._emitted.add(key)
        return False

    def boundary(self, message, path=None, line=None):
        """A security or capacity boundary is missing or breached. This fails."""
        if self._seen(message, path, line):
            return
        self.failures.append((message, path, line))
        self._emit("FAIL", BOUNDARY, message, path, line)

    def report(self, message, path=None, line=None):
        """Standing configuration is missing or unclear. This reports."""
        if self._seen(message, path, line):
            return
        self.reports.append((message, path, line))
        self._emit("REPORT", CONFIG, message, path, line)

    def skip(self, message):
        """Nothing to check -- typically the target files do not exist yet."""
        self.skipped = message
        print("[%s] SKIP: %s" % (self.name, message))

    def note(self, message):
        print("[%s] note: %s" % (self.name, message))

    # -- output ------------------------------------------------------------

    def _location(self, path, line):
        if path is None:
            return None
        location = rel(self.root, path) if os.path.isabs(str(path)) else str(path)
        if line:
            location = "%s:%s" % (location, line)
        return location

    def _emit(self, verdict, severity, message, path, line):
        location = self._location(path, line)
        prefix = "[%s] %s (%s):" % (self.name, verdict, severity)
        if location:
            print("%s %s -- %s" % (prefix, location, message))
        else:
            print("%s %s" % (prefix, message))
        self._annotate(verdict, severity, message, path, line)

    def _annotate(self, verdict, severity, message, path, line):
        """GitHub Actions annotation, so findings land on the diff itself."""
        if os.environ.get("GITHUB_ACTIONS") != "true":
            return
        level = "error" if verdict == "FAIL" else "warning"
        parts = []
        if path is not None:
            parts.append("file=%s" % (rel(self.root, path) if os.path.isabs(str(path)) else path))
            if line:
                parts.append("line=%s" % line)
        parts.append("title=%s (%s)" % (self.name, severity))
        text = message.replace("\r", " ").replace("\n", " ")
        print("::%s %s::%s" % (level, ",".join(parts), text))

    # -- finishing ---------------------------------------------------------

    def done(self):
        failures = len(self.failures)
        reports = len(self.reports)
        failing = failures > 0 or (self.strict and reports > 0)
        if self.skipped is not None and not failures and not reports:
            verdict = "SKIP"
        elif failing:
            verdict = "FAIL"
        else:
            verdict = "PASS"
        print(
            "[%s] RESULT: %s -- %d boundary failure(s), %d report(s)%s"
            % (
                self.name,
                verdict,
                failures,
                reports,
                ", strict mode" if self.strict else "",
            )
        )
        if failing:
            print("[%s] lesson: %s" % (self.name, self.lesson))
        sys.exit(1 if failing else 0)


def parse_args(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--root", help="repository root (default: two levels above this script)")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat standing-configuration reports as failures too",
    )
    return parser.parse_known_args()[0]


def main(check_name, lesson, description, body):
    """Run a check body, turning an unexpected exception into exit code 2."""
    args = parse_args(description)
    check = Check(check_name, lesson, args)
    try:
        body(check)
    except SystemExit:
        raise
    except Exception:  # a broken check must be visible, not silently green
        traceback.print_exc()
        print("[%s] RESULT: ERROR -- the check itself failed" % check_name)
        sys.exit(2)
    check.done()


# --------------------------------------------------------------------------
# A deliberately small YAML reader (standard library only)
# --------------------------------------------------------------------------


class YamlParseError(Exception):
    def __init__(self, line, message):
        Exception.__init__(self, "line %s: %s" % (line, message))
        self.line = line
        self.message = message


class _Line(object):
    __slots__ = ("indent", "text", "no")

    def __init__(self, indent, text, no):
        self.indent = indent
        self.text = text
        self.no = no


_BLOCK_SCALAR = re.compile(r"^[|>][-+]?\d*$")
_ANCHOR_OR_TAG = re.compile(r"^(?:[&*][^\s]+|![^\s]*)\s*")


def _significant(text):
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        stripped = raw.lstrip(" ")
        if stripped.startswith("#"):
            continue
        indent = len(raw) - len(stripped)
        if "\t" in raw[:indent]:
            raise YamlParseError(number, "tab in indentation")
        lines.append(_Line(indent, stripped.rstrip(), number))
    return lines


def _split_documents(lines):
    docs = []
    current = []
    for line in lines:
        if line.indent == 0 and (line.text == "---" or line.text.startswith("--- ")):
            docs.append(current)
            rest = line.text[3:].strip()
            current = [_Line(0, rest, line.no)] if rest else []
            continue
        if line.indent == 0 and line.text == "...":
            docs.append(current)
            current = []
            continue
        current.append(line)
    docs.append(current)
    return [d for d in docs if d]


def _strip_inline_comment(value):
    out = []
    quote = None
    depth = 0
    index = 0
    while index < len(value):
        char = value[index]
        if quote:
            out.append(char)
            if char == "\\" and quote == '"' and index + 1 < len(value):
                out.append(value[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
            out.append(char)
        elif char in "[{":
            depth += 1
            out.append(char)
        elif char in "]}":
            depth -= 1
            out.append(char)
        elif char == "#" and depth == 0 and (not out or out[-1] in " \t"):
            break
        else:
            out.append(char)
        index += 1
    return "".join(out).strip()


def _split_key(text):
    """Split 'key: value' respecting quotes and flow collections."""
    quote = None
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == ":" and depth == 0:
            after = text[index + 1 : index + 2]
            if after in ("", " ", "\t"):
                key = text[:index].strip()
                if key.startswith(("'", '"')) and len(key) > 1 and key[0] == key[-1]:
                    key = key[1:-1]
                return key, text[index + 1 :].strip()
        index += 1
    return None, None


def _scalar(value):
    value = _strip_inline_comment(value)
    value = _ANCHOR_OR_TAG.sub("", value, count=1).strip()
    if value == "" or value in ("null", "~", "Null", "NULL"):
        return None
    if value.startswith("[") or value.startswith("{"):
        parsed = _parse_flow(value)
        if parsed is not None:
            return parsed
    if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    lowered = value.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _parse_flow(text):
    try:
        value, index = _flow_value(text, 0)
    except Exception:
        return None
    return value if text[index:].strip() == "" else None


def _flow_value(text, i):
    while i < len(text) and text[i] in " \t":
        i += 1
    if i >= len(text):
        raise ValueError("unexpected end of flow")
    if text[i] == "[":
        i += 1
        items = []
        while True:
            while i < len(text) and text[i] in " \t,":
                i += 1
            if i < len(text) and text[i] == "]":
                return items, i + 1
            value, i = _flow_value(text, i)
            items.append(value)
    if text[i] == "{":
        i += 1
        mapping = {}
        while True:
            while i < len(text) and text[i] in " \t,":
                i += 1
            if i < len(text) and text[i] == "}":
                return mapping, i + 1
            key, i = _flow_value(text, i)
            while i < len(text) and text[i] in " \t":
                i += 1
            if i < len(text) and text[i] == ":":
                i += 1
                value, i = _flow_value(text, i)
            else:
                value = None
            mapping[str(key)] = value
    if text[i] in "\"'":
        quote = text[i]
        i += 1
        out = []
        while i < len(text) and text[i] != quote:
            out.append(text[i])
            i += 1
        return "".join(out), i + 1
    start = i
    while i < len(text) and text[i] not in ",]}":
        i += 1
    return _scalar(text[start:i].strip()), i


class _Parser(object):
    def __init__(self, lines):
        self.lines = list(lines)
        self.index = 0
        self.duplicates = []

    def peek(self):
        return self.lines[self.index] if self.index < len(self.lines) else None

    def parse_document(self):
        line = self.peek()
        if line is None:
            return None
        return self.parse_block(line.indent, [])

    def parse_block(self, indent, path):
        line = self.peek()
        if line is None or line.indent < indent:
            return None
        if line.text == "-" or line.text.startswith("- "):
            return self.parse_sequence(line.indent, path)
        return self.parse_mapping(line.indent, path)

    def parse_mapping(self, indent, path):
        result = {}
        seen = {}
        last_key = None
        while True:
            line = self.peek()
            if line is None or line.indent < indent:
                break
            if line.indent > indent:
                # A plain scalar continued on the next line, which is legal and
                # common in CRD descriptions. Fold it into the value rather
                # than treating a whole file as unreadable.
                if last_key is not None and isinstance(result.get(last_key), str):
                    result[last_key] = result[last_key] + " " + line.text
                    self.index += 1
                    continue
                raise YamlParseError(line.no, "unexpected indentation")
            if line.text == "-" or line.text.startswith("- "):
                break
            key, rest = _split_key(line.text)
            if key is None:
                raise YamlParseError(line.no, "expected a mapping key: %r" % line.text[:60])
            self.index += 1
            value = self.parse_value(rest, indent, path + [key])
            if key in seen:
                # LESSON: the later key silently discards the earlier block.
                self.duplicates.append((".".join(path + [key]), seen[key], line.no))
            seen[key] = line.no
            result[key] = value
            last_key = key
        return result

    def parse_value(self, rest, indent, path):
        rest = _ANCHOR_OR_TAG.sub("", rest, count=1).strip() if rest else rest
        if rest == "" or rest is None:
            following = self.peek()
            if following is None:
                return None
            if following.indent > indent:
                return self.parse_block(following.indent, path)
            if following.indent == indent and (
                following.text == "-" or following.text.startswith("- ")
            ):
                return self.parse_sequence(indent, path)
            return None
        if _BLOCK_SCALAR.match(rest):
            return self.consume_block_scalar(indent)
        return _scalar(rest)

    def consume_block_scalar(self, indent):
        collected = []
        base = None
        while True:
            line = self.peek()
            if line is None or line.indent <= indent:
                break
            if base is None:
                base = line.indent
            collected.append(" " * max(0, line.indent - base) + line.text)
            self.index += 1
        return "\n".join(collected)

    def parse_sequence(self, indent, path):
        items = []
        while True:
            line = self.peek()
            if line is None or line.indent < indent:
                break
            if line.indent > indent:
                # continuation of the previous plain scalar item
                if items and isinstance(items[-1], str):
                    items[-1] = items[-1] + " " + line.text
                    self.index += 1
                    continue
                break
            if not (line.text == "-" or line.text.startswith("- ")):
                break
            rest = line.text[1:].lstrip()
            column = line.indent + (len(line.text) - len(rest))
            if rest == "":
                self.index += 1
                following = self.peek()
                if following is not None and following.indent > indent:
                    items.append(self.parse_block(following.indent, path))
                else:
                    items.append(None)
                continue
            if _BLOCK_SCALAR.match(rest):
                # '- |' -- the body belongs to this item, not to the document
                self.index += 1
                items.append(self.consume_block_scalar(indent))
                continue
            key, _ = _split_key(rest)
            if key is not None:
                # '- key: value' -- rewrite as a mapping starting at the value column
                self.lines[self.index] = _Line(column, rest, line.no)
                items.append(self.parse_mapping(column, path))
                continue
            self.index += 1
            items.append(_scalar(rest))
        return items


class YamlFile(object):
    """A parsed YAML file. Never raises: inspect .error instead."""

    def __init__(self, path, text):
        self.path = path
        self.text = text
        self.docs = []
        self.duplicates = []
        self.error = None
        try:
            documents = _split_documents(_significant(text))
        except YamlParseError as exc:
            self.error = exc
            return
        except Exception as exc:  # keep the check alive on anything exotic
            self.error = YamlParseError(0, "unreadable: %s" % exc)
            return
        for lines in documents:
            # Per document, so one unreadable document in a bundle of a hundred
            # does not blind every check to the other ninety-nine.
            try:
                parser = _Parser(lines)
                self.docs.append(parser.parse_document())
                self.duplicates.extend(parser.duplicates)
            except YamlParseError as exc:
                self.error = self.error or exc
            except Exception as exc:  # noqa: BLE001
                self.error = self.error or YamlParseError(0, "unreadable: %s" % exc)

    def line_of(self, key):
        return find_line(self.text, r"^\s*['\"]?%s['\"]?\s*:" % re.escape(key), regex=True)


def load_yaml(path):
    return YamlFile(path, read_text(path))


def yaml_files(root, skip_dirs=SKIP_DIRS):
    return iter_files(root, YAML_SUFFIXES, skip_dirs)


def yaml_documents(root, skip_dirs=SKIP_DIRS):
    """Yield (YamlFile, document) for every document in every YAML file."""
    for path in yaml_files(root, skip_dirs):
        parsed = load_yaml(path)
        for doc in parsed.docs:
            if isinstance(doc, dict):
                yield parsed, doc


# -- structure helpers -----------------------------------------------------


def get(node, *keys):
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def kind_of(doc):
    value = doc.get("kind") if isinstance(doc, dict) else None
    return value if isinstance(value, str) else None


def name_of(doc):
    value = get(doc, "metadata", "name")
    return value if isinstance(value, str) else None


def api_of(doc):
    value = doc.get("apiVersion") if isinstance(doc, dict) else None
    return value if isinstance(value, str) else None


def walk(node, path=()):
    """Yield (path_tuple, value) for every node in a parsed document."""
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            for item in walk(value, path + (str(key),)):
                yield item
    elif isinstance(node, list):
        for index, value in enumerate(node):
            for item in walk(value, path + ("[%d]" % index,)):
                yield item


def strings_in(node):
    for _, value in walk(node):
        if isinstance(value, str):
            yield value


# --------------------------------------------------------------------------
# A deliberately small HCL reader (standard library only)
# --------------------------------------------------------------------------


def strip_hcl_comments(text):
    out = []
    index = 0
    quote = None
    length = len(text)
    while index < length:
        char = text[index]
        pair = text[index : index + 2]
        if quote:
            out.append(char)
            if char == "\\" and quote == '"':
                if index + 1 < length:
                    out.append(text[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char == '"':
            quote = char
            out.append(char)
            index += 1
            continue
        if char == "#" or pair == "//":
            while index < length and text[index] != "\n":
                index += 1
            continue
        if pair == "/*":
            end = text.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def hcl_blocks(text, header_pattern):
    """Yield (header, body, line) for each brace-delimited block whose header matches."""
    compiled = re.compile(header_pattern)
    for match in compiled.finditer(text):
        brace = text.find("{", match.end() - 1)
        if brace == -1:
            continue
        depth = 0
        index = brace
        while index < len(text):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        body = text[brace + 1 : index]
        line = text.count("\n", 0, match.start()) + 1
        yield match.group(0).strip(), body, line


def hcl_attr(body, name):
    match = re.search(
        r"(?m)^\s*%s\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s\n]+)" % re.escape(name), body
    )
    if not match:
        return None
    value = match.group(1).strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def hcl_objects(text, list_name):
    """Objects inside `list_name = [ { ... }, { ... } ]`, as {key: value} dicts.

    Comments are stripped first, so a commented-out pool is invisible here --
    which is correct: the kit ships its cross-location fallback pool commented
    out, and a commented pool declares nothing.
    """
    match = re.search(r"(?m)^\s*%s\s*=\s*\[" % re.escape(list_name), text)
    if not match:
        return []
    start = text.index("[", match.end() - 1)
    depth = 0
    index = start
    while index < len(text):
        if text[index] in "[{":
            depth += 1
        elif text[index] in "]}":
            depth -= 1
            if depth == 0:
                break
        index += 1
    region = text[start + 1 : index]
    objects = []
    position = 0
    while True:
        open_brace = region.find("{", position)
        if open_brace == -1:
            break
        depth = 0
        cursor = open_brace
        while cursor < len(region):
            if region[cursor] == "{":
                depth += 1
            elif region[cursor] == "}":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        body = region[open_brace + 1 : cursor]
        fields = {}
        for key, value in re.findall(
            r"(?m)^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*(\"[^\"]*\"|[^\n]+)", body
        ):
            value = value.strip().rstrip(",").strip()
            if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            fields[key] = value
        fields["_line"] = text.count("\n", 0, start + 1 + open_brace) + 1
        objects.append(fields)
        position = cursor + 1
    return objects


def hcl_files(root):
    return iter_files(root, HCL_SUFFIXES)


# --------------------------------------------------------------------------
# Vocabulary shared by several checks
#
# The kit has made its choices, but a fork may swap a component. These lists
# are what "the ingress" and "the dashboard" mean to the checks; extend them
# rather than disabling a check that stopped matching.
# --------------------------------------------------------------------------

INGRESS_NAMES = (
    "traefik",
    "ingress-nginx",
    "nginx-ingress",
    "haproxy-ingress",
    "contour",
    "envoy-gateway",
    "cilium-ingress",
)

DASHBOARD_NAMES = ("grafana",)

# The controllers that apply manifests, and therefore the ones whose identity
# is a privilege boundary.
FLUX_RECONCILERS = ("kustomize-controller", "helm-controller")

FLUX_OTHER_CONTROLLERS = (
    "source-controller",
    "notification-controller",
    "image-reflector-controller",
    "image-automation-controller",
)


def kustomize_tool():
    """('kustomize'|'kubectl', argv prefix) for building overlays, or (None, None).

    Some things cannot be checked from file text at all. A kustomize patch
    whose target matches nothing is silently ignored, and a multi-document
    JSON 6902 patch file applies only its first document -- so a check that
    reads patch text can pass while the cluster receives none of it. Those
    checks must assert on built output.
    """
    import shutil

    if shutil.which("kustomize"):
        return "kustomize", ["kustomize", "build"]
    if shutil.which("kubectl"):
        return "kubectl", ["kubectl", "kustomize"]
    return None, None


def kustomize_build(directory):
    """(rendered_text, error_text). Never raises."""
    import subprocess

    tool, argv = kustomize_tool()
    if tool is None:
        return None, "no kustomize or kubectl available"
    try:
        result = subprocess.run(
            argv + [directory],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=180,
        )
    except Exception as exc:  # noqa: BLE001
        return None, "could not run %s: %s" % (tool, exc)
    if result.returncode != 0:
        return None, result.stderr.decode("utf-8", "replace").strip()[:400]
    return result.stdout.decode("utf-8", "replace"), None


def mentions(text, names):
    lowered = text.lower()
    return [name for name in names if name in lowered]


def helm_values(doc):
    """Values of a Flux HelmRelease, or {} when there are none inline."""
    values = get(doc, "spec", "values")
    return values if isinstance(values, dict) else {}


def find_values(node, keys):
    """Yield (dotted_path, value) for every occurrence of any key in `keys`."""
    for path, value in walk(node):
        if path and path[-1] in keys:
            yield ".".join(path), value


def dashboard_scopes(docs):
    """Yield (YamlFile, label, node) for every piece of dashboard configuration.

    The dashboard is configured in several shapes -- a chart's values, a
    sub-chart's values inside an umbrella stack, a ConfigMap of ini, a
    Deployment's environment -- and a check that only understood one of them
    would pass on a cluster configured with another.
    """
    for parsed, doc in docs:
        kind = kind_of(doc) or ""
        name = (name_of(doc) or "").lower()
        if kind == "HelmRelease":
            chart = get(doc, "spec", "chart", "spec", "chart")
            chart = chart.lower() if isinstance(chart, str) else ""
            values = helm_values(doc)
            if any(d in chart for d in DASHBOARD_NAMES) or any(d in name for d in DASHBOARD_NAMES):
                yield parsed, "HelmRelease %s" % (name_of(doc) or chart), values
            for key, value in values.items():
                if key.lower() in DASHBOARD_NAMES and isinstance(value, dict):
                    yield parsed, "%s values in HelmRelease %s" % (key, name_of(doc)), value
            continue
        if kind in ("ConfigMap", "Secret", "Deployment", "StatefulSet") and any(
            d in name for d in DASHBOARD_NAMES
        ):
            yield parsed, "%s %s" % (kind, name_of(doc)), doc
            continue
        for path, value in walk(doc):
            if path and path[-1].lower() in DASHBOARD_NAMES and isinstance(value, dict):
                yield parsed, "%s in %s" % (".".join(path), os.path.basename(parsed.path)), value
