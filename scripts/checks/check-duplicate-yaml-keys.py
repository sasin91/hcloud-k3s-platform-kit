#!/usr/bin/env python3
"""LESSON: a duplicate key in a values file silently discards the earlier block.

YAML raises no duplicate-key error in most parsers -- the later key wins. A
values file can therefore contain a carefully written `resources` block that
never reaches anything, because a second occurrence of the same parent key
appears forty lines below it. The file reads as complete and applies as
something else, and nothing anywhere reports a problem.

SEVERITY: boundary. Not because a duplicate key is dangerous in itself, but
because the file no longer means what it says. Every other check in this
directory reads these files; if a file can lie, so can they.
"""

import os
import sys

sys.dont_write_bytecode = True  # no __pycache__ in a repository that does not ignore it
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

LESSON = "the later duplicate key wins and the earlier block never applies"


def body(check):
    files = 0
    for path in c.yaml_files(check.root):
        parsed = c.load_yaml(path)
        files += 1
        if parsed.error is not None:
            # A file this reader cannot parse is reported, never crashed on:
            # these checks run against a repository people are still writing.
            check.report(
                "could not parse (%s); duplicate keys in it are unchecked" % parsed.error,
                path,
                getattr(parsed.error, "line", None) or None,
            )
            continue
        for dotted, first, second in parsed.duplicates:
            check.boundary(
                "duplicate key %r -- the block at line %d is discarded by the one at line %d"
                % (dotted, first, second),
                path,
                second,
            )
    if files == 0:
        check.skip("no YAML files in the repository yet")
    else:
        check.note("inspected %d YAML file(s)" % files)


if __name__ == "__main__":
    c.main("duplicate-yaml-keys", LESSON, __doc__, body)
