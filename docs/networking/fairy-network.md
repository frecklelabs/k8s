---
description: Network design patterns for a Kubernetes homelab
---

# Fairy Network Overview

A homelab network connects user devices, Kubernetes workloads and equipment
management. The patterns below explain how to reason about those connections.

## Separate traffic requirements

Client devices need access to selected services. Workloads need service discovery
and controlled connectivity to their dependencies. Management interfaces need
restricted administrative access that remains useful during workload failures.
Define those requirements before choosing network segments and firewall rules.

A dedicated accelerator fabric can carry communication between compute devices.
Treat it as a separate connectivity requirement when planning the workload network.

## Routing and policy

A router needs routes to Kubernetes service addresses that are outside its directly
connected networks. [BGP](fairy-bgp.md) can provide those routes dynamically.
Route availability alone does not authorize a connection: review firewall policy
and return paths as well.

Some applications depend on local discovery protocols. Where a secondary network
interface is needed, scope that attachment to the application and account for its
traffic in network policy and address management.

## Operational records

Deployed topology, device models, address allocations, VLANs, peer identities and
firewall rules are maintained in private operational records. Use those records
for changes and troubleshooting; this overview does not specify live configuration.

See the [site overview](../sites/fairy/index.md) for the physical design context.
