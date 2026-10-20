---
name: kubernetes-inspection
description: Inspect allowlisted AKS workload, pod, and event health through a strictly read-only in-cluster contract
enabled: true
---

# Read-only Kubernetes inspection

Use this skill only when Kubernetes workload evidence supports an active
suspected-cause investigation, command brief, or verification question.

## Tools

- `kubernetes_get_workload`: rollout generation, replicas, readiness, and
  conditions for one exact Deployment, StatefulSet, or DaemonSet.
- `kubernetes_list_service_pods`: bounded pod phase, readiness, restart count,
  and container state for one exact service label.
- `kubernetes_list_object_events`: bounded events for one exact object.

## Boundaries

- Use only an explicitly named namespace and object/service.
- The backend enforces an allowlist of namespaces.
- These tools expose no Kubernetes mutation, exec, logs, secrets, ConfigMaps,
  environment variables, volume contents, or arbitrary API path.
- Treat 401/403 as a permission boundary, 404 as an unknown target, and timeout
  or unavailable responses as evidence gaps.
- Cite the retrieval timestamp and exact object names in the command brief.
- Never claim a restart, scale, rollout, patch, delete, or remediation occurred.
