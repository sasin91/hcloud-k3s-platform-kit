#!/bin/sh
#
# Create the two object-storage buckets the platform needs, before anything
# else exists.
#
#   1. state     — versioned, retained indefinitely
#   2. snapshots — expiring
#
# TWO BUCKETS, NOT TWO PREFIXES. They want opposite lifecycle policies. State
# wants versions kept forever, because versioning is the recovery path when a
# state file is corrupted rather than lost — which is the more common failure
# and the whole reason a remote backend beats a local one. Snapshots want
# expiry, because unbounded retention of periodic backups is a bill that grows
# forever.
#
# Put both in one bucket and the day somebody adds an expiry rule to stop paying
# for four-year-old snapshots, they are one prefix pattern away from expiring
# state history instead. That failure is silent and is discovered only when a
# recovery is attempted — and what it destroyed is the recovery path. Two empty
# buckets cost nothing. This is the cheapest blast-radius reduction in the kit.
#
# Why a script and not a bootstrap tofu configuration: a bootstrap configuration
# needs its own state file, held locally, which must not be lost. That
# reintroduces in miniature the exact problem the remote backend exists to
# solve. This script creates two things that will never change again, which is
# what a script is good at.
#
# BEFORE RUNNING: create an object-storage credential by hand in the provider
# console. That step is irreducible — the provider's CLI manages a different
# storage product and exposes no object-storage commands. There is no flag for
# it; do not go looking for one.
#
# Idempotent: safe to re-run. Existing buckets are left in place and their
# versioning and lifecycle settings are re-asserted.
#
# Not verified against a live account by the author of this file. The provider's
# published action list marks bucket creation, versioning and lifecycle as
# supported, with the documented limitation that NoncurrentVersionExpiration
# accepts only NoncurrentDays — which this script does not use.
#
# Usage:
#   AWS_ACCESS_KEY_ID=…  AWS_SECRET_ACCESS_KEY=…  ./scripts/bootstrap-buckets.sh
#
# Configuration, all overridable from the environment:
#   S3_ENDPOINT            full URL, https://<location>.your-objectstorage.com
#   S3_REGION              storage location code, e.g. fsn1
#   STATE_BUCKET           bucket for OpenTofu state
#   SNAPSHOT_BUCKET        bucket for etcd snapshots
#   SNAPSHOT_EXPIRY_DAYS   days after which a snapshot object is deleted

set -eu

# No `set -x`. Credentials are in this process's environment and the endpoint
# identifies the account; tracing this script into a terminal scrollback or a CI
# log is how they escape. Nothing below ever echoes a key, and errors are
# reported by name rather than by dumping the request.

S3_ENDPOINT="${S3_ENDPOINT:-https://fsn1.your-objectstorage.com}"
S3_REGION="${S3_REGION:-fsn1}"
STATE_BUCKET="${STATE_BUCKET:-example-tofu-state}"
SNAPSHOT_BUCKET="${SNAPSHOT_BUCKET:-example-etcd-snapshots}"

# The backstop for snapshots k3s itself will never prune — snapshots left by a
# control plane that has since been replaced, or by a cluster that no longer
# exists. Keep this comfortably LONGER than the retention configured in
# infrastructure/hetzner (etcd_snapshot_retention), or this rule starts deleting
# snapshots k3s still considers live.
SNAPSHOT_EXPIRY_DAYS="${SNAPSHOT_EXPIRY_DAYS:-30}"

die() {
    printf 'bootstrap-buckets: %s\n' "$1" >&2
    exit 1
}

info() {
    printf 'bootstrap-buckets: %s\n' "$1"
}

# --------------------------------------------------------------------------
# Preconditions. Fail on the missing thing by name, so nobody has to read a
# stack trace to learn they need a CLI or a credential.
# --------------------------------------------------------------------------

command -v aws >/dev/null 2>&1 || die \
    'the AWS CLI is required and was not found on PATH.
  This script only creates two buckets, so any S3 client would do in principle,
  but only one is implemented. Install the AWS CLI (v2) and re-run:
  https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html'

[ -n "${AWS_ACCESS_KEY_ID:-}" ] || die \
    'AWS_ACCESS_KEY_ID is not set. Create an object-storage credential by hand in
  the provider console — the provider CLI has no object-storage commands.'

[ -n "${AWS_SECRET_ACCESS_KEY:-}" ] || die \
    'AWS_SECRET_ACCESS_KEY is not set.'

[ "$STATE_BUCKET" != "$SNAPSHOT_BUCKET" ] || die \
    "STATE_BUCKET and SNAPSHOT_BUCKET must differ. They carry opposite lifecycle
  policies: one keeps every version forever, the other deletes objects after
  ${SNAPSHOT_EXPIRY_DAYS} days. Sharing a bucket puts state history within reach of an
  expiry rule."

# The provider's own documentation says to leave the CLI's addressing style at
# its default when creating buckets — i.e. path style, which is what the s3
# backend is configured for too. Do not "fix" this to virtual-hosted style
# without changing use_path_style in infrastructure/hetzner/versions.tf as well.
s3api() {
    aws s3api --endpoint-url "$S3_ENDPOINT" --region "$S3_REGION" "$@"
}

# --------------------------------------------------------------------------
# Bucket creation
# --------------------------------------------------------------------------

ensure_bucket() {
    bucket="$1"

    if s3api head-bucket --bucket "$bucket" >/dev/null 2>&1; then
        info "bucket '$bucket' already exists"
        return 0
    fi

    info "creating bucket '$bucket'"
    s3api create-bucket --bucket "$bucket" >/dev/null \
        || die "could not create bucket '$bucket'. If it exists and is owned by
  someone else, pick another name; bucket names are global to the endpoint."
}

ensure_bucket "$STATE_BUCKET"
ensure_bucket "$SNAPSHOT_BUCKET"

# --------------------------------------------------------------------------
# State bucket: versioning on.
#
# This is the difference between a recoverable corruption and an unrecoverable
# one, it is invisible until the exact moment it matters, and it is trivially
# checkable — so it is asserted below rather than assumed.
# --------------------------------------------------------------------------

info "enabling versioning on '$STATE_BUCKET'"
s3api put-bucket-versioning \
    --bucket "$STATE_BUCKET" \
    --versioning-configuration Status=Enabled >/dev/null \
    || die "could not enable versioning on '$STATE_BUCKET'"

versioning_status="$(s3api get-bucket-versioning --bucket "$STATE_BUCKET" \
    --query 'Status' --output text 2>/dev/null || true)"

[ "$versioning_status" = "Enabled" ] || die \
    "versioning on '$STATE_BUCKET' reports '${versioning_status:-none}' after being
  enabled. Do not run 'tofu init' against this bucket: an unversioned state
  bucket looks identical to a versioned one right up until you need to roll
  back a corrupted state file."

# NO LIFECYCLE RULE IS SET ON THE STATE BUCKET, and none should ever be. Every
# expiry rule this bucket has ever needed has been zero. If you find yourself
# adding one, you are about to expire the recovery path.

# --------------------------------------------------------------------------
# Snapshot bucket: expiry on.
#
# Deliberately NOT versioned. Versioning here would mean every expired snapshot
# leaves a noncurrent version behind and the bill keeps growing anyway — the
# failure the expiry rule exists to prevent.
# --------------------------------------------------------------------------

info "setting a ${SNAPSHOT_EXPIRY_DAYS}-day expiry on '$SNAPSHOT_BUCKET'"

# Written to a temp file rather than passed inline: quoting a JSON document
# through a POSIX shell into a CLI argument is a reliable source of silent
# mangling, and a malformed rule is accepted as "no rule matched".
lifecycle_json="$(mktemp)"
# shellcheck disable=SC2064
trap "rm -f '$lifecycle_json'" EXIT INT TERM

cat >"$lifecycle_json" <<EOF
{
  "Rules": [
    {
      "ID": "expire-etcd-snapshots",
      "Status": "Enabled",
      "Filter": { "Prefix": "" },
      "Expiration": { "Days": ${SNAPSHOT_EXPIRY_DAYS} },
      "AbortIncompleteMultipartUpload": { "DaysAfterInitiation": 7 }
    }
  ]
}
EOF

s3api put-bucket-lifecycle-configuration \
    --bucket "$SNAPSHOT_BUCKET" \
    --lifecycle-configuration "file://$lifecycle_json" >/dev/null \
    || die "could not set the lifecycle rule on '$SNAPSHOT_BUCKET'. Snapshots will
  accumulate forever until this succeeds — that is a bill, not an outage, so it
  is safe to fix and re-run."

# Read it back. A lifecycle rule that was accepted but not stored is
# indistinguishable from one that works, until the storage bill says otherwise.
s3api get-bucket-lifecycle-configuration --bucket "$SNAPSHOT_BUCKET" >/dev/null 2>&1 \
    || die "the lifecycle rule on '$SNAPSHOT_BUCKET' did not read back after being set."

info "done"
info "  state bucket:     $STATE_BUCKET (versioned, no expiry, ever)"
info "  snapshot bucket:  $SNAPSHOT_BUCKET (expires after ${SNAPSHOT_EXPIRY_DAYS} days)"
info ""
info "Next: put these names and the endpoint into the backend block in"
info "infrastructure/hetzner/versions.tf (a backend block takes no variables),"
info "then run 'tofu init'."
