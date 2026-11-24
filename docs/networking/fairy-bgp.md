---
description: Using BGP to connect Kubernetes service networks to a router
---

# BGP for Kubernetes Services

BGP lets a Kubernetes networking implementation advertise routes to a router.
This pattern makes service addresses reachable without placing them on a client
network's local subnet.

## Route ownership

Define which address pools the cluster may advertise and which routes the router
may accept. Keep peer identities, address pools and import policy in the private
configuration for the deployment.

For Cilium, cluster peer configuration, peer settings and advertisements are
separate resources. Review them together with service traffic policy: endpoint
placement can affect which nodes advertise a service.

## Reachability and access

Check the forward route, the return route and the allowed traffic independently.
A healthy BGP session establishes route exchange, not application health or
firewall permission. Routed pod networks also need explicit access policy.

## Verification

Confirm that peers are established, expected routes are advertised and accepted,
and a client can reach the intended service. Check endpoint availability and
withdrawal behavior before relying on route redundancy.

The [network overview](fairy-network.md) explains how routing fits the broader
network design. Deployed peer addresses, ASNs and router procedures remain in
private operational documentation.
