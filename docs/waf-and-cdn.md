# WAF and CDN

*Reference. Last reviewed 2026-08-04.*

Putting a proxying CDN in front of the cluster is one setting on their side and four decisions on yours. This page is those four decisions.

Throughout, "proxying CDN" means one that terminates TLS and forwards to your origin — Cloudflare's orange cloud, Fastly, CloudFront with a custom origin. A CDN that only serves static assets from a separate hostname changes almost nothing and is out of scope here. Examples use Cloudflare because its documentation is the most specific; the mechanisms are general.

**In this kit a proxying CDN is opt-in, not a default.** The reasons are in §6.

---

## What changes the moment you turn it on

| | Before | After |
|---|---|---|
| Who terminates TLS for your users | You | Them |
| What your origin sees as the client address | The client | Their edge |
| How ACME validates your domain | HTTP-01 works | See §1 — use DNS-01 |
| Who can reach your origin | Anyone | Anyone, until you close it (§3) |
| Who is in the request path | You | You **and** them |

Every row is a thing that can be silently wrong. Three of the four fail in ways that produce no error at the time and no alert afterwards.

---

## 1. It forces DNS-01 certificate challenges

### The mechanism

An HTTP-01 challenge is a fetch. The CA resolves your hostname, connects, and requests `http://<domain>/.well-known/acme-challenge/<token>`. Let's Encrypt documents the constraints:

> *"The HTTP-01 challenge can only be done on port 80. Allowing clients to specify arbitrary ports would make the challenge less secure, and so it is not allowed by the ACME standard."*
>
> *"Our implementation of the HTTP-01 challenge follows redirects, up to 10 redirects deep. It only accepts redirects to `http:` or `https:`, and only to ports 80 or 443."*
>
> — [Let's Encrypt, Challenge Types](https://letsencrypt.org/docs/challenge-types/)

cert-manager satisfies this by creating a temporary `acmesolver` Pod and Service plus a temporary Ingress or HTTPRoute for that path, and the documented requirement is that you *"allow your chosen Ingress controller or Gateway implementation to reach the temporary `acmesolver` Pod and Service"* ([cert-manager, HTTP01](https://cert-manager.io/docs/configuration/acme/http01/)).

Once the hostname resolves to the CDN, that fetch lands on **the CDN**, not on your gateway. Whether it ever reaches the `acmesolver` pod is decided entirely by configuration in someone else's dashboard.

### The honest version

**It is not universally true that HTTP-01 cannot complete through a proxying CDN.** If the CDN forwards `/.well-known/acme-challenge/*` to the origin unmodified and does not interpose a challenge page, it works. Many people run exactly that. Anyone telling you it is impossible is overstating it.

What *is* true, and is why this kit decides for DNS-01:

- **Success depends on settings outside your repository.** Bot protection, a managed challenge, a cache rule, an "Always Use HTTPS" redirect, or a firewall rule added months later by someone who has never heard of ACME will each break it. None of them will break it *today* — they break it at renewal.
- **The failure is delayed by up to sixty days and is silent.** You change a CDN setting in March; the certificate stops renewing in May. That is the same shape as every expensive failure in [LESSONS.md](../LESSONS.md): nothing crashed, apply succeeded, pods were green.
- **In Full (strict) mode it is chicken-and-egg.** Cloudflare's Full (strict) is *"Similar to Full Mode, but with added validation of the origin server's certificate, which can be issued by a public CA like Let's Encrypt or by Cloudflare Origin CA"* ([Cloudflare, SSL/TLS encryption modes](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/)). If the CDN will not connect to an origin without a valid certificate, you cannot use the CDN to obtain your first one. You can break the loop with a Cloudflare Origin CA certificate or by temporarily relaxing the mode — both are manual steps outside git, which is exactly what this kit tries not to have.
- **If you close the origin firewall (§3), the CDN is the only path to port 80.** HTTP-01's fate is then wholly the CDN's, with no fallback.
- **Wildcards are impossible on HTTP-01 regardless.** *"This challenge cannot be used to issue wildcard certificates."* ([Let's Encrypt](https://letsencrypt.org/docs/challenge-types/)) A per-tenant-subdomain platform reaches for a wildcard sooner or later.

### DNS-01 and what it costs

DNS-01 proves control by writing a TXT record. It never touches the request path, so the CDN is not in the loop at all. Let's Encrypt's stated advantages:

> *"You can use this challenge to issue certificates containing wildcard domain names."*
> *"It works well even if you have multiple web servers."*
> *"You can use this challenge to validate domain names whose webservers aren't exposed to the public internet."*

And its stated costs, which are real:

> *"Keeping API credentials on your web server is risky."*
> *"Your DNS API may not provide information on propagation times."*

cert-manager ships built-in solvers for ACMEDNS, Akamai, AzureDNS, CloudFlare, Google, Route53, DigitalOcean and RFC2136, with credentials supplied as a Kubernetes Secret ([cert-manager, DNS01](https://cert-manager.io/docs/configuration/acme/dns01/)).

**Mitigate the credential risk with CNAME delegation, not with careful handling.** cert-manager documents that *"the `_acme-challenge.example.com` subdomain can instead be delegated to some other, less privileged domain"* and that *"cert-manager will follow CNAME records recursively in order to determine which DNS zone to update during DNS01 challenges"*. Point `_acme-challenge.example.com` at a record in a throwaway zone, and give the cluster a credential scoped to that zone alone. A leaked credential then buys an attacker the ability to issue certificates for nothing you own — instead of the ability to repoint your MX records.

### Decision

**If the zone is proxied, use DNS-01, with `_acme-challenge` CNAME-delegated to a zone whose credential has no other authority.**

This is a guardrail candidate: if the CDN module is enabled, fail CI on a `ClusterIssuer` whose only solver is `http01`.

---

## 2. Real client addresses arrive in headers, and trusting them wrongly is a spoofing vector

### What breaks

Behind a proxying CDN every TCP connection to your origin comes from the CDN's edge. Without configuration:

- Access logs record the CDN's address for every request, uniformly.
- Rate limiting rate-limits the CDN, which means it either never triggers or blocks everyone.
- IP allowlists — for an admin path, a webhook source, an office range — silently allow the whole internet, because the address they compare against is always the same trusted proxy.
- Abuse investigation has no data at all.

### The headers

Cloudflare sets `CF-Connecting-IP` and `True-Client-IP`, and appends to `X-Forwarded-For`. Its documentation states that *"CF-Connecting-IP and True-Client-IP both have a consistent format containing only one IP address"* and recommends *"that your logs or applications look at `CF-Connecting-IP` or `True-Client-IP` instead of `X-Forwarded-For`"* ([Cloudflare, HTTP request headers](https://developers.cloudflare.com/fundamentals/reference/http-headers/)). `X-Forwarded-For` is a comma-separated list appended to by every hop, which is why picking the right element from it is a recurring source of bugs.

### The vector

**A header is a string the client chose.** If your gateway trusts `CF-Connecting-IP` or `X-Forwarded-For` from any source, then anyone who can reach your origin directly — which, before §3, is everyone — sets it to whatever they like. That defeats the allowlist, defeats the rate limiter, and poisons the audit log with attacker-chosen values, which is worse than having no log because it looks authoritative.

Cloudflare documents this shape explicitly for stacked-CDN setups: if you authenticate on `True-Client-IP` and do not add the header yourself, *"its value can be spoofed to any value"* ([Cloudflare, HTTP request headers](https://developers.cloudflare.com/fundamentals/reference/http-headers/)).

### Configuring trust

Trust must be conditioned on **the source address of the TCP connection**, and the trusted set must be the CDN's ranges — nothing wider.

- **Traefik**: on the entrypoint, `forwardedHeaders.trustedIPs` — *"Set the IPs or CIDR from where Traefik trusts the forwarded headers information (`X-Forwarded-*`)."* There is also `forwardedHeaders.insecure`, *"the insecure mode to always trust the forwarded headers information"*, of which the documentation says: *"We recommend to use this option only for tests purposes, not in production."* ([Traefik, EntryPoints](https://doc.traefik.io/traefik/reference/install-configuration/entrypoints/))
- **ingress-nginx** has equivalent ConfigMap keys (`use-forwarded-headers`, `proxy-real-ip-cidr`, `enable-real-ip`). **I did not verify their current defaults or exact semantics for this page** — read the ConfigMap reference for the version you run rather than trusting this sentence.

### The trap underneath the trap

Your gateway may never see the CDN's address either. With a `LoadBalancer` Service at the default `externalTrafficPolicy: Cluster`, kube-proxy source-NATs and the gateway sees a **node** address. A trusted-IP list containing the CDN's ranges then matches nothing, and forwarded headers are ignored — or, if someone "fixes" it by adding the node CIDR to the trusted list, every pod in the cluster becomes able to spoof client addresses.

Kubernetes documents the alternative: *"If you set `service.spec.externalTrafficPolicy` to the value `Local`, kube-proxy only proxies requests to local endpoints… This approach preserves the original source IP address. If there are no local endpoints, packets sent to the node are dropped"* ([Kubernetes, Using Source IP](https://kubernetes.io/docs/tutorials/services/source-ip/)).

So: `externalTrafficPolicy: Local` on the gateway Service, plus a gateway on every node that the load balancer targets, or you have a scheduling constraint that presents as intermittent connection failures. If your load balancer supports PROXY protocol, that is the other route to the same result — **I have not verified the provider-side specifics of that path and it is not what this kit ships.**

### Decision

**Trust forwarded headers only from the CDN's published ranges, and close the origin (§3) at the same time.** The two are one change. A trusted-range list without a locked origin is not a defence — it is the only thing between an attacker and a forged client address, and it is defeated by connecting to your origin directly. Trusting from the wrong source is strictly worse than not trusting at all, because it converts a missing feature into a working bypass.

Guardrail candidate: fail CI if `forwardedHeaders.insecure` is true, or if a trusted-IP list contains a private or node CIDR.

---

## 3. Origin exposure, and why the firewall matters

### Proxying hides the origin from DNS. That is all it hides.

The address leaks through, at minimum:

- **Historical DNS.** Passive-DNS archives keep what your zone said before you proxied it. Turning the orange cloud on does not retract anything.
- **Any unproxied record in the same zone.** A mail record, a monitoring hostname, a staging name, an SSH bastion — if it resolves to the same host or the same subnet, the CDN in front of `www` is decoration.
- **Certificate Transparency.** Let's Encrypt states: *"Let's Encrypt submits all certificates we issue to CT logs."* ([Let's Encrypt, Certificate Transparency](https://letsencrypt.org/docs/ct-logs/)) CT logs are append-only public records and are routinely searched, so **every hostname you obtain a certificate for is discoverable**, including internal-sounding names you assumed nobody would guess. Whether a given name then resolves publicly is a separate question — but the name is public. *(That CT logs are widely mirrored and searchable is well known and is the point of the system; Let's Encrypt's page states the submission, not the searchability, so treat the second half as general knowledge rather than a cited claim.)*
- **Outbound connections your origin makes.** A webhook, an outbound e-mail, an error report to a third-party service, an OCSP fetch — each reveals the address to whoever receives it.
- **Scanning.** Someone enumerating a provider's ranges and matching TLS certificates or response bodies finds you without any of the above.

**Conclusion: treat the origin address as public and defend it, rather than treating concealment as the defence.** Concealment that you cannot verify is not a control.

### Lock the firewall to the CDN's ranges

At the network level, outside the host. Hetzner Cloud Firewalls default correctly for this: *"If you do not set any rule, all inbound traffic will automatically be blocked and all outbound traffic will automatically be permitted."* ([Hetzner, Cloud Firewalls](https://docs.hetzner.com/cloud/firewalls/overview/)) So the change is an explicit inbound allow for 80/443 restricted to the CDN's ranges, rather than a deny added to an open default.

Cloudflare publishes its ranges at [cloudflare.com/ips](https://www.cloudflare.com/ips/), described there as *"the definitive source of Cloudflare's current IP ranges"*, with an API endpoint for machine-readable retrieval.

**Automate the refresh.** The page carries an update history, so the list changes. **I could not find a Cloudflare-documented change cadence or a notification mechanism**, which means the refresh interval is your problem to choose. Note the asymmetry in how a stale list fails:

- A **new** CDN range you have not allowed produces visible errors for some users. Annoying; discoverable.
- A **decommissioned** range you still allow is invisible. If the block is reassigned to someone else, you have an allow rule pointing at a stranger. Nothing reports this, ever.

The second is the one to build the automation around.

### The firewall alone is not enough — and this is the non-obvious part

An allowlist of a shared CDN's ranges permits **every other customer of that CDN**. Anyone can sign up, point their own zone at your origin address, and reach you from an allowed range. Your firewall proves the request came from Cloudflare; it does not prove it came from *your* Cloudflare.

Cloudflare's answer is Authenticated Origin Pulls, which *"helps ensure requests to your origin server come from the Cloudflare network, which provides an additional layer of security"* — the problem being that *"Without AOP, anyone who discovers your origin server's IP address can send requests directly, bypassing Cloudflare and all its protections"* ([Cloudflare, Authenticated Origin Pulls](https://developers.cloudflare.com/ssl/origin-configuration/authenticated-origin-pull/)).

And read the caveat, because it is the whole point: **global AOP proves Cloudflare, not your account.** To guarantee requests come from your zone you must use zone-level or per-hostname AOP with your own certificate.

This is precisely the failure that [LESSONS.md](../LESSONS.md) describes as *"two layers where the inner one is disabled is one layer wearing a costume"*. A CDN-range firewall plus global AOP looks like two independent controls and is one, because both are satisfied by "some Cloudflare customer". AOP with your own certificate is the layer that actually distinguishes you.

### Decision

**Firewall restricted to CDN ranges, refreshed automatically, plus per-zone AOP with your own certificate.** If you will not do the AOP part, be clear-eyed that your control boundary is "customers of this CDN", and size your origin-side authentication accordingly.

---

## 4. WAF: theirs or yours

### The CDN's own

Cloudflare's managed rulesets are the Cloudflare Managed Ruleset, the Cloudflare OWASP Core Ruleset (*"Cloudflare's implementation of the Open Web Application Security Project (OWASP) ModSecurity Core Rule Set"*), and Exposed Credentials Check. The Managed and OWASP rulesets require a paid plan (Pro, Business or Enterprise); the Cloudflare Free Managed Ruleset is *"Available on all Cloudflare plans"* and covers *"high-impact and widely exploited vulnerabilities"* ([Cloudflare, Managed Rules](https://developers.cloudflare.com/waf/managed-rules/)).

**For:** runs at the edge, so blocked traffic never costs you bandwidth, CPU or a pod. Zero operational burden. Sees real client addresses natively, with no forwarded-header configuration to get wrong. Rule updates arrive without you doing anything.

**Against:** you cannot read the rules, cannot test against them offline, cannot reproduce a block locally, and cannot version them in the repository next to what they protect. False positives are debugged in a dashboard by whoever has the login. Every "the WAF is blocking our API" incident is an out-of-band investigation rather than a diff.

### Self-hosted, as middleware

The credible open-source engine is **OWASP Coraza**: *"an open source, enterprise-grade, high performance Web Application Firewall… written in Go, supports ModSecurity SecLang rulesets and is 100% compatible with the OWASP Core Rule Set."*

Read its own maturity table before planning around it. Coraza documents its integrations as: Caddy plugin (stable), proxy-Wasm for Envoy-family proxies (stable, under development), HAProxy SPOE (preview), **Traefik plugin (preview)**, Gin middleware (preview), Apache and nginx (experimental) ([Coraza, Introduction](https://coraza.io/docs/tutorials/introduction/)).

That matters here: for a Traefik-fronted cluster, the integration you would use is documented as *preview*. **The WAF would be less mature than the proxy it runs inside.** That is a legitimate reason to decline, and it is not a criticism of Coraza — it is reading the label.

*I could not verify the current maturity, authorship or support status of the Traefik plugin catalogue entry for Coraza; the Traefik plugin site did not return usable content. Check it yourself before adopting.*

**Do not build a WAF plan on ingress-nginx + ModSecurity.** The ingress-nginx maintainers have announced a move to maintenance mode — *"1.13 in all likelihood will be the last minor release"*, with continued patch releases for dependencies and security only, and migration to InGate expected to *"take about 2 years"* ([kubernetes/ingress-nginx#13002](https://github.com/kubernetes/ingress-nginx/issues/13002)). A control whose only integration path has an announced end is a migration you have already signed up for.

**For (self-hosted):** rules live in git next to the workload; identical in staging and production; no vendor dependency; you can run in detection mode and read what *would* have been blocked with full request context.

**Against:** it is a service you now operate, tune and page on. It consumes cluster CPU on traffic you intended to discard. And unless §2 is already correct, **it sees the CDN's address for every request** — so its rate limiting and IP rules are wrong until forwarded-header trust is configured. Order matters: §2 before §4.

### The third option, which is usually the best value

Do not deploy a generic ruleset at all.

Most of what a Core Rule Set install catches in practice is caught by: request size limits, per-route rate limits, blocking obviously hostile user agents and paths, and a short list of targeted rules for the framework you actually run. That is a few dozen lines, it produces almost no false positives, and you understand every line of it.

CRS in blocking mode without a tuning period **will** break legitimate traffic — file uploads, rich-text bodies, anything with SQL-like or shell-like strings in a legitimate field. The standard outcome is that someone disables it during an incident and it never comes back on, which leaves you with the cost, the config, and none of the protection. If you are going to run CRS, run it in detection mode for a month first, and budget the tuning as real work.

### Decision

**Start with rate limits, size limits and targeted rules in the cluster. Add the CDN's free managed ruleset if you are already proxying. Reach for a self-hosted generic ruleset only when you have a specific threat it addresses and someone who will own the tuning.**

---

## 5. What you give up by proxying

Stated as plainly as the benefits usually are.

- **A third party terminates TLS.** They see the plaintext of every request and response — session cookies, tokens, form bodies, personal data. This is not a risk, it is the operating model; encryption to the origin is a *second* connection, not a tunnel. If you handle health, legal, financial or credential data, the CDN is inside your compliance scope and needs the same paperwork as any other processor.
- **They are in the request path.** Their outage is your outage, at every layer, for every hostname you proxied. You cannot engineer around it from inside the cluster.
- **Failover is slower than you think, and ordered.** To bypass the CDN you must **first** open the origin firewall, **then** unproxy the DNS record — in that order, or you are down between the two steps. And the DNS change is bounded by TTL, not by how fast you type. Write this runbook before you need it; the moment you need it is the moment nobody remembers which order.
- **Two pieces of configuration become load-bearing and fail silently**: DNS-01 issuance (§1) and forwarded-header trust (§2). Both work fine right up until they do not, and neither announces itself.
- **Cache correctness becomes your problem.** A cache rule scoped slightly too wide serves one logged-in user's page to everyone. There is no cluster-side symptom — your application logs one request and looks healthy while the edge serves the response a thousand times.
- **The parts that matter cost money.** The free tier is genuinely useful; the managed rulesets that people mean when they say "we have a WAF" are on paid plans ([Cloudflare, Managed Rules](https://developers.cloudflare.com/waf/managed-rules/)).
- **Long-lived connections get limits you cannot read.** See [websockets.md §4](websockets.md) — the WebSocket idle timeout is undocumented, and the HTTP path has a documented 125 s ceiling.

### What you get, which is not nothing

- Volumetric DDoS absorbed before it reaches a link you pay for. On a small cluster this is the single strongest argument, because the alternative is not "degraded service" — it is your provider null-routing the address.
- TLS terminated near the user, with modern cipher support and HTTP/3, without you maintaining any of it.
- A global cache in front of an origin that has one region.
- Bot management, which is genuinely hard to build.
- Origin address concealment as a side effect — worth having as long as you do not mistake it for §3.

---

## 6. The decision for this kit

**A proxying CDN is opt-in, and turning it on is four changes made together, not one.**

If you enable it:

1. **DNS-01 issuance**, with `_acme-challenge` CNAME-delegated to a zone whose credential can do nothing else.
2. **Forwarded-header trust** restricted to the CDN's published ranges — and `externalTrafficPolicy: Local` or an equivalent, so the gateway actually sees those ranges.
3. **Origin firewall** restricted to the same ranges, refreshed automatically, **plus per-zone Authenticated Origin Pulls with your own certificate.**
4. **A WAF decision made explicitly**, edge or in-cluster, with an owner named for tuning.

Items 1–3 are not defence in depth if only some are done. Each one is the thing that makes the next one meaningful: DNS-01 removes the CDN from certificate issuance; the firewall makes header trust safe; AOP makes the firewall mean *your* CDN account rather than *a* CDN account.

Each is also mechanically checkable, which is where it belongs — in the generator and in CI, not in this page. Prose describing a security posture drifts from the posture. Candidate guardrails:

- If the CDN module is enabled, fail on a `ClusterIssuer` whose only solver is `http01`.
- Fail if `forwardedHeaders.insecure` is true anywhere.
- Fail if a trusted-proxy range includes a private, node or pod CIDR.
- Scheduled check: the firewall's allow ranges match the CDN's current published list.

---

## Sources

- [Let's Encrypt — Challenge Types](https://letsencrypt.org/docs/challenge-types/)
- [Let's Encrypt — Certificate Transparency](https://letsencrypt.org/docs/ct-logs/)
- [cert-manager — HTTP01 solver](https://cert-manager.io/docs/configuration/acme/http01/)
- [cert-manager — DNS01 solver](https://cert-manager.io/docs/configuration/acme/dns01/)
- [Cloudflare — SSL/TLS encryption modes](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/)
- [Cloudflare — Authenticated Origin Pulls](https://developers.cloudflare.com/ssl/origin-configuration/authenticated-origin-pull/)
- [Cloudflare — HTTP request headers](https://developers.cloudflare.com/fundamentals/reference/http-headers/)
- [Cloudflare — Managed Rules](https://developers.cloudflare.com/waf/managed-rules/)
- [Cloudflare — IP Ranges](https://www.cloudflare.com/ips/)
- [Traefik — EntryPoints reference](https://doc.traefik.io/traefik/reference/install-configuration/entrypoints/)
- [Kubernetes — Using Source IP](https://kubernetes.io/docs/tutorials/services/source-ip/)
- [Hetzner — Cloud Firewalls](https://docs.hetzner.com/cloud/firewalls/overview/)
- [OWASP Coraza — Introduction](https://coraza.io/docs/tutorials/introduction/)
- [kubernetes/ingress-nginx — maintenance mode announcement](https://github.com/kubernetes/ingress-nginx/issues/13002)
