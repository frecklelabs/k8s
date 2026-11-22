---
description: General network segmentation and Kubernetes connectivity patterns
---

# Fairy Network Design

Separate networks by trust and traffic requirements. This page describes design
patterns; it is not a site topology, firewall specification or addressing plan.

## Segmentation

Use distinct segments for workloads with different trust requirements. Define
permitted flows explicitly rather than assuming that devices sharing a segment
should be able to reach each other.

| Traffic class | Design consideration |
|---------------|----------------------|
| User devices | Allow access to the services they consume |
| Guest devices | Keep visitor access separate from internal services |
| Appliances | Permit only the integrations and destinations they require |
| Infrastructure management | Restrict access to administrative identities and tools |
| Compute and storage | Size capacity for measured workload demand |

Keep actual segment names, VLAN identifiers, subnets, routing policies and
physical connections in private configuration and operations documentation.

## Kubernetes connectivity

Use Kubernetes Services and Gateways for ordinary application traffic. Evaluate
a secondary network interface only when a workload needs protocols that cannot
cross its normal network boundary. Such an interface needs its own access policy
and should not provide unrestricted access to other segments.

## Routing services

A router needs routes to service addresses outside its connected networks.
[BGP](fairy-bgp.md) can advertise those routes dynamically. Verify return paths
and firewall policy as well as route availability.

## Discovery and DNS

Give clients stable service aliases so replacing a host does not require client
configuration changes. Enable discovery across network boundaries only for the
specific services and consumers that need it.

## Maintenance

Before a network change, check current private inventory and configuration.
Verify the intended application path after the change, including DNS, routing,
access controls and observed link capacity. Historical diagrams are not evidence
of the current wiring.
