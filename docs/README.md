# Documentation

Reference pages and architecture decision records for the kit. The [root README](../README.md) says what is installed; [LESSONS.md](../LESSONS.md) says what was learned getting there. These pages are the detail behind specific questions.

Every non-obvious claim is cited to primary documentation. Where something could not be verified, the page says so rather than asserting it.

## Reference

| Page | Answers |
|---|---|
| [websockets.md](websockets.md) | Do WebSockets work through the ingress? (Yes, transparently.) Then: idle and read/write timeouts, sticky sessions, a CDN's request-duration ceiling, buffering middleware that breaks streaming — and why a reverse proxy is a shared restart domain, with the criteria for routing around it entirely. |
| [waf-and-cdn.md](waf-and-cdn.md) | What changes when you put a proxying CDN in front of the cluster: why it forces DNS-01 certificate challenges, how to trust forwarded client addresses without creating a spoofing vector, why the origin firewall must be locked to the CDN's ranges *and* why that alone is not enough, and self-hosted WAF middleware versus the CDN's own. Includes what you give up by proxying. |

## Decisions

Architecture decision records. Numbered, immutable once accepted — superseded rather than edited.

| ADR | Decision |
|---|---|
| [0001](adr/0001-metrics-stack.md) | VictoriaMetrics stack over Prometheus/Thanos/Loki, for metrics and logs. Includes the retention threshold at which the decision becomes wrong. |

Decisions not recorded here were worked as tickets; start at the [decision map](../../../issues/1).

## A note on status

Consistent with the root README: almost nothing in this repository has been verified against a running cluster. Where a page states a measurement it carries a date. Where it states a vendor's benchmark, it says so. Treat everything else as a documented property of the software, which is a weaker claim than a tested one.
