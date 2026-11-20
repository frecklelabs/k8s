---
description: A pattern-level overview of the Fairy homelab site
---

# Fairy Site

Fairy is a homelab environment for Kubernetes workloads and experimentation.
This overview connects the physical design concepts to the topic guides.

## Physical design

Plan compute, networking and power together: allow room for airflow, cable
access and equipment maintenance. Keep management access available when the
workload network is unavailable, and document power dependencies before moving
equipment.

Rack elevations, device identities, hardware inventory and cabling records are
maintained privately. This page is an architecture overview rather than an
equipment list or installation plan.

## Networking

Treat client access, workload traffic and equipment management as distinct
requirements. Routing supplies reachability; firewall policy determines which
connections are allowed. See the [network overview](../../networking/fairy-network.md)
and [BGP design notes](../../networking/fairy-bgp.md) for these patterns.

## Related guides

- [Compute](../../compute/index.md) — compute platform guides
- [Storage](../../storage/index.md) — storage design and operations
- [Runbooks](../../runbooks/index.md) — operational procedures
