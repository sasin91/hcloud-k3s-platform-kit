terraform {
  # OpenTofu, not Terraform. The `encryption` block below is OpenTofu-only, and
  # that is the whole reason for the choice: state AND plan files are encrypted
  # client-side before they leave this machine, so the bucket only ever holds
  # ciphertext. Terraform has no equivalent, and it will fail on this block —
  # which makes "run it with tofu" enforced rather than documented.
  #
  # >= 1.10.1 is the module's own floor, and also the line where the s3 backend
  # gained `use_lockfile` (native conditional-write locking, no lock table).
  required_version = ">= 1.10.1"

  required_providers {
    hcloud = {
      source = "hetznercloud/hcloud"
      # Pinned to the current major. An unpinned dependency is a latent breaking
      # change: the next `init -upgrade` is free to cross a major boundary and
      # rename inputs under you. Same rule as the module pin in main.tf.
      version = "~> 1.62"
    }
  }

  # ---------------------------------------------------------------------------
  # State: S3-compatible object storage at the same provider as the cluster.
  #
  # A backend block is NOT part of the variable system — every value here must
  # be a literal. That is the one place in this kit where the endpoint is
  # duplicated (see `object_storage_endpoint_host` in variables.tf, consumed by
  # etcd's snapshot config). Two places that can disagree eventually will, so
  # they are called out in both directions: change one, change the other.
  #
  # The alternative is partial configuration — delete the three lines marked
  # PLACEHOLDER and pass them at init instead:
  #   tofu init -backend-config=backend.hcl
  # which keeps bucket names and endpoints out of a public repository entirely.
  #
  # Explicitly rejected: the `kubernetes` backend. Storing state in a Secret
  # inside the cluster it manages means losing the cluster loses the means to
  # rebuild it, and it cannot exist at create time.
  # ---------------------------------------------------------------------------
  backend "s3" {
    bucket = "example-tofu-state" # PLACEHOLDER — from scripts/bootstrap-buckets.sh
    key    = "hetzner/cluster.tfstate"

    endpoints = {
      s3 = "https://fsn1.your-objectstorage.com" # PLACEHOLDER — <location>.your-objectstorage.com
    }
    region = "fsn1" # PLACEHOLDER — the storage location code, not an AWS region

    # Verified against this provider on 2026-08-03: conditional writes return
    # 412 on a contended lock, so native locking is real from the first init.
    # There is no lock table to create and none to forget to create.
    use_lockfile = true

    # The provider's own docs say to leave the CLI's addressing style at its
    # default when creating buckets, i.e. path style. The bootstrap script and
    # this backend therefore agree on path style rather than disagreeing
    # silently on bucket names that happen to be DNS-safe.
    use_path_style = true

    # This is not AWS: there is no STS, no account id endpoint, and the region
    # is a storage location code that AWS's validator has never heard of. Each
    # of these skips a check that can only produce a false failure here.
    skip_credentials_validation = true
    skip_region_validation      = true
    skip_requesting_account_id  = true

    # Deliberately ABSENT: skip_s3_checksum.
    # Default checksum behaviour was verified working against this provider on
    # 2026-08-03. Setting the flag "just in case" would disable an integrity
    # check that demonstrably works — and a workaround outlives its cause, so
    # the next reader would find a disabled integrity check that looks
    # deliberate. If you ever need it, record the failing response here.
  }

  # ---------------------------------------------------------------------------
  # Client-side encryption of state and plan.
  #
  # Applied to BOTH `state` and `plan`. A plan file contains the same secrets
  # the state does; encrypting only one of them is a half-open door.
  #
  # These values must resolve during `tofu init`, before any state exists, so
  # supply the passphrase from the environment rather than a prompt:
  #   export TF_VAR_state_passphrase='…at least 16 characters…'
  # Losing this passphrase means losing the ability to read your own state.
  # Store it wherever you store the age key — same class of secret, same fate.
  #
  # PBKDF2 from a passphrase is the single-operator default because it needs no
  # other infrastructure. OpenBao is the documented upgrade path for a team;
  # swapping the key provider is a local change to this block.
  # ---------------------------------------------------------------------------
  encryption {
    key_provider "pbkdf2" "default" {
      passphrase = var.state_passphrase
    }

    method "aes_gcm" "default" {
      keys = key_provider.pbkdf2.default
    }

    state {
      method = method.aes_gcm.default
    }

    plan {
      method = method.aes_gcm.default
    }
  }
}
