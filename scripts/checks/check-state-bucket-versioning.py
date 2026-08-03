#!/usr/bin/env python3
"""LESSON: versioning on the state store is the recovery path for corruption.

State that exists only on one machine will eventually be lost, and a
configuration that cannot manage what it built is worse than none at all -- it
looks authoritative while being inert. A remote backend fixes loss. Versioning
fixes the *other* failure, corruption, which is more common and from which
there is otherwise no way back.

It is invisible until the moment it matters, and at that moment it cannot be
enabled retroactively.

The companion rule, also checked here: opposite lifecycles must not share a
bucket. State wants versions kept indefinitely; periodic snapshots want expiry,
because unbounded retention is a bill that grows forever. Put both in one
bucket and the day somebody adds an expiry rule to stop paying for old
snapshots, they are one prefix pattern away from expiring state history
instead. What that destroys is the recovery path, and it is discovered only
when recovery is attempted.

SEVERITY:
  boundary -- a state bucket with no versioning; state and snapshots sharing
              one bucket while an expiry rule exists.
  config   -- versioning configured by a mechanism this check cannot tie to a
              specific bucket. Report rather than guess: an unreadable check
              result is not a failure, but it is not a pass either.
"""

import os
import re
import sys

sys.dont_write_bytecode = True  # no __pycache__ in a repository that does not ignore it
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

LESSON = "versioning is the recovery path for a corrupted state file, and cannot be added later"

VERSIONING_PATTERNS = (
    r"put-bucket-versioning",
    r"PutBucketVersioning",
    r"mc\s+version\s+enable",
    r"versioning_configuration",
    r"<Status>\s*Enabled\s*</Status>",
    r"Status=Enabled",
)

EXPIRY_PATTERNS = (
    r"put-bucket-lifecycle",
    r"PutBucketLifecycle",
    r"lifecycle_rule",
    r"mc\s+ilm\s+",
    r"expiration",
)

BUCKET_TOKEN = re.compile(
    r"(?:--bucket[= ]+|s3://)([\"']?)([A-Za-z0-9._$\{\}-]+)\1|\$\{?([A-Z_]*BUCKET[A-Z_]*)\}?"
)

SCRIPT_SUFFIXES = (".sh", ".bash")


def bucket_near(lines, index, window=4):
    """The bucket a call targets, looked for across continuation lines.

    Shell calls to an object-storage CLI are routinely spread over several
    lines with backslashes, so the bucket is rarely on the same line as the
    verb. Reporting "cannot tell which bucket" for every such call would make
    this check noise, and noise is how a check gets ignored.
    """
    for offset in range(0, window + 1):
        for candidate in {index + offset, index - offset}:
            if candidate < 0 or candidate >= len(lines):
                continue
            match = BUCKET_TOKEN.search(lines[candidate])
            if match:
                name = match.group(2) or match.group(3)
                # "$STATE_BUCKET", "${STATE_BUCKET}" and "STATE_BUCKET" are the
                # same bucket; comparing them raw would miss a shared one.
                return name.lstrip("$").strip("{}")
    return None


def scan(root):
    """(files, versioning_hits, expiry_hits, backend_found)"""
    versioning, expiry = [], []
    backend = []
    files = 0
    for path in c.iter_files(root, SCRIPT_SUFFIXES + c.HCL_SUFFIXES + (".md",)):
        if path.endswith(".md"):
            continue
        files += 1
        text = c.read_text(path)
        lines = text.splitlines()
        for index, line in enumerate(lines):
            number = index + 1
            if line.lstrip().startswith("#"):
                continue
            for pattern in VERSIONING_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    versioning.append((path, number, line.strip(), bucket_near(lines, index)))
                    break
            for pattern in EXPIRY_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    expiry.append((path, number, line.strip(), bucket_near(lines, index)))
                    break
        if path.endswith(c.HCL_SUFFIXES):
            stripped = c.strip_hcl_comments(text)
            for _, block, line in c.hcl_blocks(stripped, r'(?m)^\s*backend\s+"s3"'):
                backend.append((path, line, c.hcl_attr(block, "bucket")))
    return files, versioning, expiry, backend


def body(check):
    files, versioning, expiry, backend = scan(check.root)
    if files == 0:
        check.skip("no bootstrap scripts or OpenTofu configuration yet")
        return
    if not backend and not versioning and not expiry:
        check.skip("no state backend or bucket bootstrap declared yet")
        return

    if backend:
        for path, line, bucket in backend:
            check.note("state backend declared in %s (bucket: %s)" % (c.rel(check.root, path), bucket))

    if not versioning:
        target = backend[0][0] if backend else None
        check.boundary(
            "a state bucket is configured but nothing enables versioning on it; a corrupted "
            "state file would be unrecoverable",
            target,
            backend[0][1] if backend else None,
        )
        return

    for path, number, line, bucket in versioning:
        if bucket is None:
            check.report(
                "versioning is enabled here, but this check cannot tell which bucket it "
                "applies to: %s" % line[:100],
                path,
                number,
            )
        else:
            check.note(
                "versioning enabled on %s (%s:%d)" % (bucket, c.rel(check.root, path), number)
            )

    versioned_buckets = {b for _, _, _, b in versioning if b}
    for path, number, line, bucket in expiry:
        if bucket and bucket in versioned_buckets:
            check.boundary(
                "an expiry rule and versioning both target bucket %r -- state history and "
                "snapshot retention have opposite lifecycles and must not share a bucket"
                % bucket,
                path,
                number,
            )
        elif bucket is None:
            check.report(
                "an expiry rule is configured but this check cannot tell which bucket it "
                "applies to: %s" % line[:100],
                path,
                number,
            )


if __name__ == "__main__":
    c.main("state-bucket-versioning", LESSON, __doc__, body)
