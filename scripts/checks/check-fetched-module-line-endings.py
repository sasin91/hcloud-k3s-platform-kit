#!/usr/bin/env python3
"""Fail if a FETCHED module contains carriage returns.

RUN THIS BETWEEN `tofu init` AND `tofu apply`. It inspects downloaded modules,
which do not exist before init and are re-fetched by `init -upgrade`.

WHY IT EXISTS
-------------
Git on Windows converts text files to CRLF on checkout by default, and that
default applies to modules a build tool clones on your behalf. Your own
repository's .gitattributes does not govern somebody else's repository.

Two distinct failures follow, and neither says anything about line endings:

  1. A policy source (.te) is compiled ON THE NODE at first boot. The compiler
     rejects the carriage return, the unit fails, and k3s never starts -- while
     every server reports healthy and SSH works. The only visible symptom is an
     opaque provisioner error two layers above the real one.

  2. A .tf file's string literals become the scripts uploaded by remote-exec
     provisioners. `set -e` arrives as `set -e\\r`, and the shell reports
     `set: -: invalid option`. Observed: 543 CR lines in one module .tf, four
     nodes created, every provisioner failing.

WHY A CHECK AND NOT A README LINE
---------------------------------
There WAS a README line. It said to set core.autocrlf=false before init. The
person who wrote that line hit failure (2) twelve hours later by running
`init -upgrade` in a shell where the guard was not exported.

Documentation drifts; checks do not. This is the same lesson the repository
already records, applied to the repository itself.

THE FIX when this fails:

    git config --global core.autocrlf false     # or export GIT_CONFIG_GLOBAL
    rm -rf .terraform/modules
    tofu init
    tofu destroy && tofu apply                  # if nodes were already built

The destroy is not optional if nodes exist: user_data sits under
lifecycle.ignore_changes, so a corrupted boot configuration is not corrected by
re-applying. It has to be recreated.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

# Files whose content is executed, compiled or parsed on a Linux host. A CR in
# any of these is fatal somewhere downstream; a CR in a .md is not.
EXTENSIONS = (".tf", ".tfvars", ".sh", ".te", ".yaml", ".yml", ".tpl", ".service")

# Where build tools place fetched modules.
MODULE_DIRS = (".terraform/modules", ".terragrunt-cache")


def body(check):
    roots = []
    for base, _dirs, _files in os.walk(check.root):
        for module_dir in MODULE_DIRS:
            candidate = os.path.join(base, *module_dir.split("/"))
            if os.path.isdir(candidate) and candidate not in roots:
                roots.append(candidate)
        # Do not descend into a module directory we have already recorded.
        if os.path.basename(base) == "modules" and ".terraform" in base:
            _dirs[:] = []

    if not roots:
        check.skip(
            "no fetched modules found -- run this after `tofu init`, which is "
            "the only point at which the files it checks exist"
        )
        return

    offenders = []
    scanned = 0
    for root in roots:
        for base, _dirs, files in os.walk(root):
            for name in files:
                if not name.endswith(EXTENSIONS):
                    continue
                path = os.path.join(base, name)
                scanned += 1
                try:
                    with open(path, "rb") as handle:
                        blob = handle.read()
                except OSError:
                    continue
                count = blob.count(b"\r\n")
                if count:
                    offenders.append((path, count))

    check.note("scanned %d executable/parsed file(s) in %d fetched module tree(s)"
               % (scanned, len(roots)))

    if not offenders:
        check.note("no carriage returns in any fetched module")
        return

    offenders.sort(key=lambda item: -item[1])
    for path, count in offenders[:10]:
        check.boundary(
            "%s contains %d CRLF line ending(s). On a Linux node this file is "
            "executed, compiled or parsed, and the carriage return breaks it "
            "with an error that names neither the file nor the line ending"
            % (os.path.relpath(path, check.root), count)
        )
    if len(offenders) > 10:
        check.note("... and %d more file(s)" % (len(offenders) - 10))

    check.note(
        "FIX: git config --global core.autocrlf false && rm -rf .terraform/modules "
        "&& tofu init. If nodes were already created, destroy and recreate them -- "
        "user_data is under lifecycle.ignore_changes and a re-apply will not fix it"
    )


LESSON = ("a dependency is fetched by a tool with its own opinions; "
          "pin what you fetch and how you fetch it")

if __name__ == "__main__":
    c.main("fetched-module-line-endings", LESSON, __doc__, body)
