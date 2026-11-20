---
description: Reusable naming conventions for hosts, fixed infrastructure and services
---

# Naming Conventions

Names identify provisioned systems and roles. Keep physical components and their
serial numbers in private inventory records.

## Hosts

Use a kind followed by a sequence number:

```text
{kind}{sequence}
```

Keep host names independent of the site and rack so moving equipment does not
force a rename. Give a replacement host a new identity and retain the previous
record for history.

## Fixed infrastructure

Use location and role for equipment whose identity describes a position:

```text
{location}-{role}{sequence}
```

A replacement unit can inherit that position's name while its hardware history
remains in the inventory. Keep actual locations and device assignments private.

## Sites and clusters

Record a host's site in its DNS domain and inventory rather than embedding it in
the host name. Kubernetes cluster names can follow `{site}-k8s{sequence}`.
Use cluster names when referring to Kubernetes resources.

## Services

Give clients stable DNS aliases for service roles. An alias can move to a new
host without changing client configuration.

## Management interfaces

A separate management interface can use `{host}-mgmt`. Its existence, access
path and recovery procedure belong in private operations documentation.
