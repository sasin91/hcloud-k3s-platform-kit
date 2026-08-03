# WebSockets

*Reference. Last reviewed 2026-08-04.*

This is the first question everyone asks, so here is the linkable answer.

**The upgrade works. Nothing else about it does, by default.**

---

## 1. The upgrade is transparent and needs no configuration

A WebSocket connection starts as an ordinary HTTP/1.1 request carrying `Upgrade: websocket` and `Connection: Upgrade`; the server answers `101 Switching Protocols` and the same TCP connection is then used for frames ([RFC 6455 §1.3, §4.1](https://www.rfc-editor.org/rfc/rfc6455#section-4.1)). To a reverse proxy this is a request that never ends. There is no separate listener, no separate port, no separate route type, and no annotation that turns it on.

- ingress-nginx documents this plainly: *"Support for websockets is provided by NGINX out of the box. No special configuration required."* ([ingress-nginx, Miscellaneous](https://kubernetes.github.io/ingress-nginx/user-guide/miscellaneous/))
- Gateway API routes WebSockets as ordinary HTTP traffic — `HTTPRoute` is the correct and only route kind you need for it, and `HTTPRoute` is in the Standard channel ([Gateway API guides](https://gateway-api.sigs.k8s.io/guides/)).
- For Traefik, **I could not find a current documentation page that states this either way.** The absence of any WebSocket configuration surface is consistent with "nothing to configure", and that is how this kit is written — but treat it as unverified until you have watched a `101` come back through your own gateway.

So: if your WebSocket does not connect at all, the cause is almost never the upgrade. It is TLS, routing, or the application.

If it connects and then *dies*, read on. Everything below is a real cause.

---

## 2. Idle, read and write timeouts

A proxy cannot tell an idle WebSocket from a hung backend. Every timeout it applies to "a request that is taking a long time" applies to a connection that is *supposed* to take a long time.

### The numbers, from primary docs

| Proxy | Setting | Default | Documented meaning |
|---|---|---|---|
| nginx | `proxy_read_timeout` | `60s` | *"a timeout for reading a response from the proxied server. The timeout is set only between two successive read operations… If the proxied server does not transmit anything within this time, the connection is closed."* ([nginx](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_read_timeout)) |
| ingress-nginx | `proxy-read-timeout`, `proxy-send-timeout` | `60s` | *"The only requirement to avoid the close of connections is the increase of the values… A more adequate value to support websockets is a value higher than one hour (`3600`)."* ([ingress-nginx](https://kubernetes.github.io/ingress-nginx/user-guide/miscellaneous/)) |
| Traefik | `transport.respondingTimeouts.readTimeout` | `60s` | *"the maximum duration for reading the entire request, including the body."* ([Traefik](https://doc.traefik.io/traefik/reference/install-configuration/entrypoints/)) |
| Traefik | `transport.respondingTimeouts.writeTimeout` | `0s` | *"Maximum duration before timing out writes of the response."* |
| Traefik | `transport.respondingTimeouts.idleTimeout` | `180s` | *"Maximum duration an idle (keep-alive) connection will remain idle before closing itself."* |

Note the shape of the nginx wording: the timeout is **between successive reads**, not on total duration. That is why a heartbeat fixes it.

**Unverified:** whether Traefik's `readTimeout` — documented as covering "reading the entire request" — applies to a connection that has been upgraded and is no longer an HTTP request, is not stated in Traefik's documentation. Do not assume either answer. Open a connection, send nothing, and time how long it survives; that measurement is worth more than this table.

### What to actually do

1. **Raise the proxy timeouts** on whatever entrypoint or ingress class carries long-lived traffic. Not globally — see §5.
2. **Send heartbeats from the application**, and treat this as the primary defence rather than the fallback. RFC 6455 defines Ping and Pong frames for exactly this ([RFC 6455 §5.5.2–5.5.3](https://www.rfc-editor.org/rfc/rfc6455#section-5.5.2)). A ping every 30 s keeps every idle timer in the path reset, including the ones you do not know about.
3. Do not rely on TCP keepalive. It operates below the proxy's read timer, which counts *application* bytes.

> A raised timeout protects you from the proxies you configured. A heartbeat protects you from the ones you did not know were in the path — a corporate proxy, a mobile carrier's NAT, a CDN. Configure both, and if you only get one, take the heartbeat.

---

## 3. Sticky sessions

First, separate two things that get conflated:

- **Which pod serves this connection.** Not a problem for the WebSocket itself. One connection is one TCP stream to one pod for its whole life. There is nothing to be sticky about.
- **Which pod serves the *next* connection.** This is the actual problem. It appears on reconnect, on the HTTP handshake if your client library falls back to long-polling (socket.io, SockJS), and on any sibling HTTP request that must see the same in-memory state.

You need affinity **only if a server holds session state in its own memory.** If state lives in Redis, Valkey, Postgres or a pub/sub bus, any replica can serve any reconnect and affinity is dead weight that also breaks your load distribution.

> Persisting state makes it durable, not shared — and sticky sessions are what you reach for when you have confused the two. Fix the state model if you can afford to; use affinity if you cannot.

### The mechanisms available

| Layer | Mechanism | Notes |
|---|---|---|
| Service | `sessionAffinity: ClientIP` | Default is `None`; `sessionAffinityConfig.clientIP.timeoutSeconds` defaults to `10800` (3 h) ([Service API reference](https://kubernetes.io/docs/reference/kubernetes-api/service-resources/service-v1/)). Keys on **client IP**, so it is useless behind a proxy — every request arrives from the gateway pod's address and all users collapse onto one backend. |
| Gateway API | `sessionPersistence` (GEP-1619) | Cookie-based is Core support, header-based is Extended. **The GEP is in the Experimental state** ([GEP-1619](https://gateway-api.sigs.k8s.io/geps/gep-1619/)) — check your implementation actually supports it before designing around it. |
| Traefik | Cookie stickiness on the service | *"a `Set-Cookie` header is set on the initial response to let the client know which server handles the first response."* Configured per service; if the named server becomes unhealthy the request is forwarded elsewhere and the cookie is updated. Default cookie `MaxAge` is zero, i.e. it never expires ([Traefik service load balancing](https://doc.traefik.io/traefik/reference/routing-configuration/http/load-balancing/service/)). |
| CDN | CDN-level affinity | Cloudflare documents that with load-balanced origins you should enable session affinity so that WebSocket reconnections do not land on a server without the session state ([Cloudflare, WebSockets](https://developers.cloudflare.com/network/websockets/)). |

### The trap

Cookie-based stickiness requires the client to send cookies. A browser `WebSocket` does send cookies for the handshake on the same origin. A native mobile client, a Go or Rust service client, or anything using a bare WebSocket library **usually does not**, unless you wrote the cookie jar yourself. Affinity that works in the browser and silently does not work for your mobile app is the standard version of this bug.

---

## 4. A proxying CDN's request-duration ceiling

If you put a proxying CDN in front of the cluster (see [waf-and-cdn.md](waf-and-cdn.md)), it becomes another restart domain and another set of timers — with the added problem that you cannot read its configuration from your repository.

For Cloudflare specifically:

- **WebSockets are a per-zone setting.** Cloudflare documents it as supported on all plans, and as something you turn on ([Cloudflare, WebSockets](https://developers.cloudflare.com/network/websockets/)). Check it is on before debugging anything else.
- **Ordinary HTTP requests have a hard ceiling.** Error 524 is raised when *"the origin did not provide an HTTP response before the default 125 seconds Proxy Read Timeout"*, and there is a *"30 seconds Proxy Write Timeout"* ([Cloudflare, Error 524](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-524/)). This kills long-polling fallbacks, streamed downloads and slow API calls — but it is a *duration* limit, and a heartbeat does not help you.
- **WebSockets get an idle limit instead, and its value is not published.** Cloudflare states only that it *"will close a WebSocket connection when no data is transmitted in either direction for a period of time"* and that Enterprise customers can have a custom value configured. **The widely repeated "100 seconds" figure is not in Cloudflare's documentation and I could not verify it from a primary source.** Assume the limit is short, unpublished, and subject to change without notice — which is another way of saying: heartbeat.
- Cloudflare's own remedy for work that exceeds the HTTP ceiling is to *move it to a DNS-only (unproxied) subdomain*. That is the same conclusion as §6, arrived at from their side.

If you need a guarantee rather than an observation, put the WebSocket on an unproxied hostname. A limit you cannot read and cannot pin is not a limit you can design against.

---

## 5. Buffering middleware breaks streaming

Buffering is the default in more places than people expect, and it does not break WebSockets — it breaks everything *next to* them, which is why it shows up in the same debugging session.

- **nginx**: `proxy_buffering` defaults to **on**. With it on, *"nginx receives a response from the proxied server as soon as possible, saving it into the buffers… If the whole response does not fit into memory, a part of it can be saved to a temporary file on the disk."* With it off, *"the response is passed to a client synchronously, immediately as it is received."* ([nginx](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering))
- **Traefik's Buffering middleware**: *"Traefik reads the entire request into memory (possibly buffering large requests into disk), and rejects requests that are over a specified size limit."* And explicitly: *"When the middleware is attached, Traefik buffers the request body before forwarding it. As a result, Traefik can send the request upstream with a fixed `Content-Length` instead of streaming the original chunked body."* ([Traefik, Buffering](https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/buffering/))

Consequences:

- **Server-Sent Events and chunked streaming stop being live.** Events arrive in a lump when the buffer flushes or the response ends. The endpoint is not broken and nothing logs an error — it just is not a stream any more.
- **Streaming uploads become non-streaming.** A middleware that must know `Content-Length` has to read the whole body first, so a request-body size limit and a streaming upload endpoint are mutually exclusive by construction.
- **Compression middleware has the same shape.** Anything that must see enough bytes to compress will hold them.

The rule: **a size-limiting or transforming middleware on the same router as a streaming endpoint is a bug, whether or not it has fired yet.** Attach those middlewares per-route, never at the entrypoint.

---

## 6. The decided constraint: a reverse proxy is a shared restart domain

This is the part of this page that is a decision rather than a lookup.

> **A reverse proxy is a shared restart domain.** It terminates every connection passing through it, so restarting it destroys them all at once.

### The mechanism

Every connection through the proxy is a TCP connection whose remote end is the proxy process. When that process exits, every one of those connections is gone. There is no handoff, no draining that a long-lived connection can survive, and no amount of graceful-shutdown configuration that changes this — because graceful shutdown is a *deadline*, not a reprieve:

- Kubernetes grants a pod a termination period that *"defaults to 30 seconds"*, after which the container is killed ([Kubernetes, Pod Lifecycle](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/)).
- Traefik's `transport.lifeCycle.graceTimeOut` defaults to **10 s** and is documented as *"the duration to give active requests a chance to finish before Traefik stops. In this time frame no new requests are accepted."* ([Traefik](https://doc.traefik.io/traefik/reference/install-configuration/entrypoints/))

A request that takes 200 ms finishes inside that window. A session held open for six hours never will. Grace periods bound how long you wait before killing the connection; they do not save it.

### What restarts a proxy

All of these are routine, and none of them is "an incident":

- A configuration change — a new middleware, a changed timeout, a TLS option.
- A chart or image upgrade, including a patch release picked up by automated dependency updates.
- A node drain for unattended OS patching or a coordinated node upgrade.
- Autoscaler scale-down consolidating nodes.
- Any eviction the PodDisruptionBudget permits.

### Why it is invisible until it is not

For short HTTP requests this costs nothing measurable. Connections are sub-second and clients retry; a rolling restart is not visible in any dashboard. **The proxy's restart schedule is therefore set by the tolerance of the short-lived traffic, which has effectively infinite tolerance.** Nobody is choosing to disrupt the WebSockets — nobody is thinking about them at all.

For long-lived connections the same event means every user disconnected. And it is worse than once: during a rolling update a client that reconnects may land on a replica that has not been replaced yet, and be disconnected again when its turn comes. **Worst case, a client is disconnected once per replica.**

Then the reconnects arrive together. Every client that was connected at the moment of the restart attempts to reconnect within the same second, against a proxy that is still coming up. If reconnection is not jittered, the outage caused by the restart is shorter than the outage caused by the recovery. Exponential backoff **with jitter** is not a nicety here; it is the difference between a blip and a self-inflicted denial of service.

### The rule

> **Do not put long-lived connection protocols behind the same proxy as short-lived ones.** They have incompatible tolerance for restarts, and the short-lived ones set the schedule.

---

## 7. When to route around the proxy entirely

Use these criteria, not intuition. **Any two of the following and you should not be on the shared gateway:**

1. Sessions are measured in **hours**, not seconds.
2. A reconnect is **user-visible** — lost game state, a dropped editor session, a re-authentication prompt — rather than a retry nobody notices.
3. The protocol **is not HTTP**. Raw TCP gets no hostname multiplexing, which is the main thing a shared HTTP proxy is *for*. You are paying the shared restart domain and receiving nothing.
4. You need **a port per tenant**, which the shared gateway cannot express anyway.
5. You want the workload's uptime **decoupled from ingress maintenance** — so that patching the platform is not a customer-visible event.

### The three options, in increasing order of separation

**a. A second gateway deployment.** Same software, same charts, different Deployment and different ingress class or `GatewayClass`. Costs one more pod per replica. Buys you a separate restart domain, separate timeouts, and separate upgrade cadence — you can leave it alone for a month while you iterate on the main one. **This is the cheapest correct answer and is usually the right one.**

**b. A `LoadBalancer` Service straight to the workload.** The cloud load balancer forwards to the pods with no HTTP proxy in the path at all. This is what this kit does for its example tenants: an explicitly declared port pair per realm, bypassing the ingress proxy entirely. Restarting ingress does nothing to it.

Costs, stated honestly:
- No hostname multiplexing — you spend a port, not a name, per service.
- TLS terminates in the application or at the load balancer, so certificate delivery is your problem and cert-manager's `Certificate` has to be consumed by the workload rather than the gateway.
- Port allocation becomes a value consumed in two places (the Service and whatever advertises the endpoint). **Declare it once and render both from it.** Never number ports by index — deleting one tenant renumbers every tenant after it.
- Do not give the first tenant the protocol's conventional default port. It will work, it will look like the template has a sensible default, and the second tenant will hit a collision that reads as a bug rather than a design limit.
- Source IP: with the default `externalTrafficPolicy: Cluster`, kube-proxy SNATs and the client address is lost; `Local` preserves it but *"If there are no local endpoints, packets sent to the node are dropped"* ([Kubernetes, Using Source IP](https://kubernetes.io/docs/tutorials/services/source-ip/)). If the workload authenticates or rate-limits on client address, you must choose `Local` deliberately and accept the scheduling constraint.

**c. `TCPRoute` / `TLSRoute` through the shared Gateway — which is a trap.** These exist, but they are in the Experimental channel: *"The experimental release channel includes everything in the standard release channel plus some experimental resources and fields. This includes TCPRoute, TLSRoute, and UDPRoute"*, and *"future releases of the API could include breaking changes to experimental resources and fields"* ([Gateway API guides](https://gateway-api.sigs.k8s.io/guides/)).

More importantly: routing raw TCP **through the shared gateway** puts the connection back inside the shared restart domain. It looks like it solves the problem because the protocol is no longer HTTP, but the restart semantics are identical. It solves multiplexing, not lifetime. If your reason for leaving the HTTP path was §6, this option does not address it.

---

## Checklist

Before shipping anything that holds a connection open:

- [ ] Application sends Ping frames on an interval shorter than the shortest timeout in the path — and shorter than the CDN's unpublished one.
- [ ] Proxy read/write timeouts raised on the entrypoint carrying this traffic, and **not** raised globally.
- [ ] Client reconnects with exponential backoff **and jitter**.
- [ ] No buffering, size-limit or compression middleware attached at the entrypoint level.
- [ ] Session state is shared, or affinity is configured with a mechanism your actual clients honour — verified with the non-browser client, not just the browser.
- [ ] Decided explicitly: shared gateway, dedicated gateway, or bypass. Written down with which of the §7 criteria applied.
- [ ] If a proxying CDN is in front: WebSockets enabled on the zone, or the hostname is unproxied.
- [ ] Measured, not assumed: open a connection, send nothing, record how long it survives. Do this again after any ingress change.

---

## Sources

- [RFC 6455 — The WebSocket Protocol](https://www.rfc-editor.org/rfc/rfc6455)
- [ingress-nginx — Miscellaneous (Websockets)](https://kubernetes.github.io/ingress-nginx/user-guide/miscellaneous/)
- [nginx — ngx_http_proxy_module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)
- [Traefik — EntryPoints reference](https://doc.traefik.io/traefik/reference/install-configuration/entrypoints/)
- [Traefik — Buffering middleware](https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/buffering/)
- [Traefik — Service load balancing](https://doc.traefik.io/traefik/reference/routing-configuration/http/load-balancing/service/)
- [Kubernetes — Service API reference](https://kubernetes.io/docs/reference/kubernetes-api/service-resources/service-v1/)
- [Kubernetes — Pod Lifecycle](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/)
- [Kubernetes — Using Source IP](https://kubernetes.io/docs/tutorials/services/source-ip/)
- [Gateway API — Guides (release channels)](https://gateway-api.sigs.k8s.io/guides/)
- [Gateway API — GEP-1619 Session Persistence](https://gateway-api.sigs.k8s.io/geps/gep-1619/)
- [Cloudflare — WebSockets](https://developers.cloudflare.com/network/websockets/)
- [Cloudflare — Error 524](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-524/)
