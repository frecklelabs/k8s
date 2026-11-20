---
description: Naming patterns for hosts, fixed infrastructure and clusters
---

# Naming Conventions

Names identify provisioned things; inventory records track the physical hardware
and its history. The site belongs in the domain rather than the hostname.

## Hosts

Use a machine kind and sequence number. Keep the hostname and Kubernetes node
name consistent. Retire host names rather than reusing them after a rebuild;
retain prior identities in private inventory history.

Hardware identity comes from its serial number and inventory ID. Replaceable
parts have inventory records without needing their own hostnames.

## Fixed infrastructure

Use a location and role for equipment tied to a fixed position. A replacement
unit can keep the position's name while the inventory records the retired unit
and replacement serials. Record moves and parts changes separately.

## Service aliases and clusters

Clients use service DNS aliases so hardware replacement does not change the
address they use. Cluster identities have their own lifecycle and need not follow
physical-site renames.

Exact deployed names, rack assignments and address mappings are maintained in
private operational records.
