#!/bin/sh
# Delete the provider resources the CLUSTER created, which OpenTofu does not
# know about and `tofu destroy` therefore cannot remove.
#
# WHY THIS EXISTS
#
# Three components in this kit create billable provider resources at runtime:
#
#   cluster-autoscaler  -> servers
#   cloud controller    -> load balancers   (one per LoadBalancer Service)
#   CSI driver          -> volumes          (one per PersistentVolumeClaim)
#
# None of them appear in OpenTofu state, because OpenTofu did not create them.
# `tofu destroy` walks its state and removes what it finds, reports success for
# exactly that, and leaves the rest running and billing.
#
# It is worse than a leak. An autoscaler-created server stays attached to the
# private network, so destroying the NETWORK fails -- and the destroy dies
# partway through with an error about the network, several steps from the
# actual cause. Observed on a live cluster: destroy errored on a network it
# could not delete because of a node it could not see.
#
# ORDER MATTERS. Load balancers and volumes must go before the servers they
# attach to, and everything must go before `tofu destroy` runs.
#
# USAGE
#   export HCLOUD_TOKEN=...
#   scripts/teardown.sh            # list only, deletes nothing
#   scripts/teardown.sh --delete   # actually delete
#   tofu destroy                                   # THEN this
#
# This script is deliberately list-by-default. It deletes billable
# infrastructure and there is no undo.

set -eu

DELETE=0
[ "${1:-}" = "--delete" ] && DELETE=1

: "${HCLOUD_TOKEN:?set HCLOUD_TOKEN (needs write scope to delete)}"
API="https://api.hetzner.cloud/v1"

api() {
    # $1 method, $2 path -- response body on stdout
    curl -sS -X "$1" -H "Authorization: Bearer $HCLOUD_TOKEN" \
         -H "Content-Type: application/json" "$API$2"
}

# CHECK THE STATUS CODE, NOT curl's EXIT CODE. curl exits 0 for a 409 just as
# happily as for a 204: the request succeeded, the operation did not. The first
# version of this script reported "ok" for a volume delete the provider had
# refused because the volume was still attached to a server -- which is exactly
# the reported-success failure this entire repository is about, committed by the
# script written to explain it.
api_status() {
    curl -sS -o /dev/null -w '%{http_code}' -X "$1" \
         -H "Authorization: Bearer $HCLOUD_TOKEN" \
         -H "Content-Type: application/json" "$API$2"
}

python_bin=""
for candidate in python3 python py; do
    if "$candidate" -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
        python_bin="$candidate"; break
    fi
done
[ -n "$python_bin" ] || { echo "FAIL: no working Python 3 interpreter." >&2; exit 2; }

# Selects only resources this kit's cluster components create. A server is
# cluster-created when its name carries the autoscaler's marker; load balancers
# and volumes are matched by the naming the cloud controller and CSI driver use.
list_ids() {
    api GET "/$1?per_page=50" | "$python_bin" -c '
import json,sys,re
kind=sys.argv[1]; pattern=sys.argv[2]
data=json.load(sys.stdin)
for item in data.get(kind, []):
    name=str(item.get("name",""))
    if re.search(pattern, name):
        print("%s\t%s" % (item["id"], name))
' "$2" "$3"
}

# Volumes first: a volume attached to a server blocks that server's deletion.
# Load balancers next. Servers last.
#
# CSI volumes are named pvc-<uuid> by Kubernetes.
# CCM load balancers are named from the Service, so match anything not
# obviously OpenTofu-managed -- reviewed by the operator in list mode.
echo "== volumes created by the CSI driver =="
VOLS=$(list_ids volumes volumes '^pvc-' || true)
echo "${VOLS:-  (none)}"

echo "== load balancers created by the cloud controller =="
LBS=$(list_ids load_balancers load_balancers '.' || true)
echo "${LBS:-  (none)}"

echo "== servers created by the autoscaler =="
SRVS=$(list_ids servers servers '\-autoscaled\-' || true)
echo "${SRVS:-  (none)}"

if [ "$DELETE" -eq 0 ]; then
    echo
    echo "LIST ONLY. Nothing was deleted. Re-run with --delete to remove these,"
    echo "then run 'tofu destroy'. Deleting these AFTER destroy does not work:"
    echo "destroy fails on the network they are still attached to."
    exit 0
fi

drop() { # $1 path, $2 "id\tname" lines
    printf '%s\n' "$2" | while IFS="$(printf '\t')" read -r id name; do
        [ -n "${id:-}" ] || continue

        # A volume attached to a server cannot be deleted. Detach first and let
        # the action settle; otherwise the provider refuses and there is nothing
        # in an exit code to tell you so.
        if [ "$1" = "volumes" ]; then
            api_status POST "/volumes/$id/actions/detach" >/dev/null 2>&1 || true
            sleep 3
        fi

        printf '  deleting %s %s ... ' "$1" "$name"
        code=$(api_status DELETE "/$1/$id" 2>/dev/null || echo 000)
        case "$code" in
            2*)  echo "ok" ;;
            404) echo "already gone" ;;
            *)   echo "FAILED (HTTP $code)" ;;
        esac
    done
}

echo
echo "Deleting, in dependency order."
drop volumes "$VOLS"
drop load_balancers "$LBS"
drop servers "$SRVS"

echo
echo "Done. Now run 'tofu destroy'. Re-run this script afterwards to confirm"
echo "nothing remains -- a teardown you have not re-listed is not a teardown"
echo "you have verified."
