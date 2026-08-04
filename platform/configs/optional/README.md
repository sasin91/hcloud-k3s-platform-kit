# Optional configuration — not applied by default

Everything here requires a value only you can supply, and is therefore **not** in
the default apply path.

## Why these are separated

The platform layer reconciles with `wait: true` and no explicit health checks,
which means it waits for **every** resource it applies to become ready. That is
the right default: it turns "the API server accepted this" into "this actually
works", and the layers behind it — observability, tenants — depend on that
guarantee.

It also means a resource that *cannot* become ready blocks everything behind it,
permanently.

Certificate issuers cannot become ready without a real contact address. The
authority rejects a placeholder outright:

```
Failed to register ACME account: 400 urn:ietf:params:acme:error:invalidContact
```

Shipping them in the default path therefore produced a cluster where
observability and tenants never reconciled, on a fresh install, until somebody
edited a file. The failure was several layers from the cause: an unusable email
address became an unresolvable certificate, which became an invalid Gateway
listener, which became a Kustomization that never finished.

> A placeholder you must edit is fine. A placeholder that gates the whole
> reconciliation chain is a design fault — the cost of leaving it unedited
> should be the feature that needs it, not everything downstream of it.

## Enabling them

1. Set a real contact address in `cluster-issuers.yaml`. Both of them.
2. Apply the staging issuer first and confirm a certificate is issued. The
   production authority has rate limits that are unforgiving of a cluster you
   are still rebuilding.
3. Uncomment the HTTPS listener in `../gateway.yaml`.
4. Only then add these to the parent kustomization.

Do them in that order. Enabling the listener before the issuer works recreates
exactly the deadlock this directory exists to avoid.
