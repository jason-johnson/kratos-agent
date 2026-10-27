# Kratos AKS Deployment Plan

Last updated: 2026-10-22

## Purpose

Deploy the completed Agent Reliability Engineer application into the provisioned
AKS environment, validate it under its production authorization boundaries, and
leave a repeatable operating and rollback path.

This document tracks deployment decisions and progress. The procedural command
reference remains [deploy/aks/README.md](deploy/aks/README.md), and the completed
application simplification work remains in [PLAN.md](PLAN.md).

## Safety and scope

- AKS is the only supported application runtime.
- Deployment is a manual operator action; CI must not install or upgrade Helm.
- Use immutable image digests. Do not deploy mutable tags.
- Do not commit endpoints, Azure IDs, credentials, connection strings, private
  values, rendered manifests, TLS material, Kubernetes Secrets, or private feed
  configuration.
- ServiceNow is the only external system allowed to mutate. Azure,
  Application Insights, Kubernetes, and Microsoft Graph remain read-only.
- Keep OBO disabled for the initial core deployment. Enable it only through the
  dedicated OBO phase after Entra consent and delegated-access tests are ready.
- Stop and investigate any authorization, ownership, stale-data, partial-result,
  or coverage-gap failure. Do not weaken the boundary to make acceptance pass.

## Current verified baseline

- [x] The `ktaks` infrastructure deployment succeeds from the manual GitHub OIDC
  workflow.
- [x] AKS is provisioned with OIDC issuer and Workload Identity enabled.
- [x] Cilium, Azure Policy, Key Vault CSI, Defender, managed Prometheus,
  Container Insights, and Web Application Routing are enabled.
- [x] Cosmos DB, Blob Storage, Key Vault, Foundry, Application Insights, Log
  Analytics, private endpoints, and private DNS are provisioned.
- [x] The backend Workload Identity and managed-service role assignments exist.
- [x] The AKS kubelet has ACR pull access.
- [x] The deployment identity has ACR push access.
- [x] Private Python and npm feeds work from GitHub OIDC.
- [x] Backend, frontend, and OBO images build through approved private feeds.
- [x] All three images pass their smoke tests.
- [x] All three images pass the fixable HIGH/CRITICAL Trivy gate.
- [x] All three images are published to ACR with the successful commit SHA.
- [ ] Application Helm resources are installed in AKS.
- [ ] DNS, TLS, and Entra user authentication are configured.
- [ ] Live functional, authorization, resilience, restore, and rollback
  acceptance is complete.

## Decision register

| ID | Decision | Status | Notes |
|---|---|---|---|
| DP-001 | Deploy the application with the checked-in Helm chart | Confirmed | Infrastructure and application delivery remain separate. |
| DP-002 | Use immutable ACR digests for every deployed image | Confirmed | Backend, frontend, OBO, and auth proxy must be digest-pinned. |
| DP-003 | Keep OBO disabled for the first core deployment | Confirmed | The image is published, but Graph infrastructure, consent, and workload deployment are deferred. |
| DP-004 | Mirror a patched official auth-proxy image into ACR | Confirmed, blocked | Wait for an official release that passes the fixable HIGH/CRITICAL gate; do not maintain an internal fork or weaken the gate. |
| DP-005 | Grant AKS operator access to a dedicated Entra group | Confirmed | The dedicated group exists; its object ID remains protected and is parameterized through Bicep. |
| DP-006 | Deliver secrets through Key Vault CSI | Confirmed | Separate backend and auth identities and synchronized Secrets are implemented; environment secret objects remain pending. |
| DP-007 | Use the Web Application Routing ingress class | Confirmed | Already enabled on the cluster and selected by the chart. |
| DP-008 | Require TLS and Entra OIDC before user traffic | Confirmed | No unauthenticated temporary public deployment. |
| DP-009 | Install and upgrade Helm manually with `--atomic` | Confirmed | Failed releases must roll back automatically. |
| DP-010 | Stop AKS when deployment work pauses | Proposed | Reduces non-production compute cost; start it before Helm or acceptance work. |
| DP-011 | Use Workload Identity and OTLP for Application Insights | Confirmed | Do not create or deliver an Application Insights connection-string secret. |
| DP-012 | Use an Azure regional DNS label and Let's Encrypt | Confirmed | Use the existing Application Routing load balancer with a GA `cloudapp.azure.com` hostname and cert-manager-managed TLS. No purchased domain, Azure DNS zone, or preview approval is required. |
| DP-013 | Install cert-manager separately with mirrored immutable images | Confirmed | The manual workflow scans and mirrors cert-manager v1.21.3 components; the operator installs the official versioned chart with protected ACR digests. |

## Required operator decisions

Record decisions here before creating environment-specific resources.

| Input | Status | Selected value or reference |
|---|---|---|
| Entra operator group | Selected | Dedicated `kratos-aks-operators` group; do not record its object ID in this file. |
| Public hostname | Pending allocation | Use an Azure-provided regional `cloudapp.azure.com` DNS label; do not record the generated hostname in this public repository. |
| Azure DNS zone and record owner | Not required | Azure publishes the regional DNS label on the existing load balancer. |
| TLS certificate source | Selected | Let's Encrypt through separately installed cert-manager; test staging before production issuance. |
| Secret delivery model | Selected | Key Vault CSI with separate backend and auth identities. |
| Entra login application owner | Selected | Existing deployment service principal using `Application.ReadWrite.OwnedBy`. |
| Users/groups allowed to sign in | Selected | Reuse `kratos-aks-operators` for initial acceptance by assigning the group to the enterprise application. |
| Auth-proxy release | Blocked | Official v7.15.5 has fixable Go TLS/X.509 and HTTP findings. |
| ServiceNow target and credentials owner | Blocked | Credentials are not ready; values must remain outside Git and live ServiceNow acceptance cannot proceed until they are available. |
| Initial live acceptance operators | Pending | |

## Phase 1 - Establish operator access

Goal: allow an approved Entra group to perform manual Helm operations without
using cluster local accounts or sharing the deployment service principal.

- [x] Select or create the Entra operator group.
- [x] Add a parameterized Azure Kubernetes RBAC role assignment in Bicep.
- [x] Store `AKS_OPERATOR_PRINCIPAL_ID` in the protected GitHub `ktaks`
  environment.
- [x] Apply the Bicep change to the live environment.
- [x] Verify an operator can authenticate with `az aks get-credentials`.
- [x] Verify the operator can inspect the target namespace and perform Helm
  operations.
- [x] Verify local AKS accounts remain disabled.
- [ ] Document break-glass ownership outside the repository.

Exit gate:

- An approved operator can reach the cluster through Entra authentication.
- No personal ID or environment-specific object ID is committed.

## Phase 2 - Resolve and pin release images

Goal: produce a private release manifest containing only immutable image
coordinates.

- [x] Resolve the backend ACR digest for the successful commit SHA.
- [x] Resolve the frontend ACR digest for the successful commit SHA.
- [x] Retain the OBO digest for later use, but keep `obo.enabled: false`.
- [ ] Select a supported OAuth2 Proxy release. Official v7.15.5 is currently
  blocked by fixable HIGH findings.
- [x] Scan official OAuth2 Proxy v7.15.5 with the same HIGH/CRITICAL gate; it
  failed and was not approved.
- [ ] Mirror the auth-proxy image into ACR.
- [ ] Resolve its ACR digest.
- [ ] Store repositories and digests in a protected values file outside Git.
- [ ] Verify no image uses `latest` or a tag without a digest.

Exit gate:

- Backend, frontend, and auth proxy have approved ACR repository/digest pairs.
- The release can be recreated without pulling an application image from a
  public registry.

## Phase 3 - Configure Entra user authentication

Goal: require authenticated Entra users at ingress before the application is
exposed.

- [x] Approve the Azure regional public hostname and store it outside Git.
- [x] Create a confidential Entra application for OAuth2 Proxy.
- [x] Add a parameterized Graph/Bicep definition for the exact
  `https://<host>/oauth2/callback` redirect URI.
- [x] Configure the application for the deployment tenant only.
- [x] Require assignment and declare the approved operator-group assignment.
- [ ] Create the client credential through the approved secret-management
  process.
- [ ] Configure logout and post-logout behavior.
- [ ] Record only non-secret operational ownership outside the repository.

Exit gate:

- The login application is tenant-restricted and has an approved owner.
- Client credentials are stored in the approved secret manager, not Git.

## Phase 4 - Implement secret delivery

Goal: provide application and auth-proxy secrets without committing or exposing
them.

Preferred implementation:

- [x] Extend the Helm chart with backend and auth `SecretProviderClass`
  resources.
- [x] Use the existing Key Vault CSI add-on and Workload Identity.
- [x] Grant the auth workload only Key Vault Secrets User; retain the backend's
  existing bounded managed-service roles.
- [x] Synchronize separate Kubernetes Secrets only for components that require
  environment-backed secret keys.
- [x] Ensure secret names and keys remain stable across upgrades.
- [x] Verify synthetic rendering does not expose secret values.
- [ ] Verify live mounted secret files and synchronized Secrets are not exposed by
  logs, rendered manifests, or status output.
- [ ] Document rotation and recovery procedures.

Required secret categories:

- OAuth2 Proxy client ID, client secret, and cookie secret.
- ServiceNow client credentials.
- Application Insights connection string is not required; use Workload Identity
  and OTLP configuration.
- Any Copilot runtime credential that cannot be replaced by federated identity.

Decision gate:

- If Key Vault CSI is not selected, document approval for a manually created
  existing Kubernetes Secret and its rotation procedure before deployment.

Exit gate:

- Pods can consume required secrets.
- No secret value appears in Git, Helm values, command arguments, or logs.

## Phase 5 - Configure DNS and TLS

Goal: expose one approved HTTPS endpoint through Web Application Routing.

- [x] Select the GA Azure regional DNS-label path; no Azure DNS zone is
  required.
- [x] Add a protected operator utility that configures the supported
  `NginxIngressController` resource without printing or committing the
  hostname.
- [x] Select Let's Encrypt and add a single-owner `ClusterIssuer` chart path.
- [x] Add manual workflow gates to scan and mirror all pinned cert-manager
  images.
- [x] Run the workflow with `publish_platform_images` selected.
- [x] Start AKS, allocate the DNS label, and store the generated hostname in a
  mode-`0600` protected file outside the repository.
- [x] Install cert-manager from the official versioned chart using protected
  ACR repositories and digests.
- [x] Verify staging certificate issuance before switching to production.
- [ ] Set the ingress host and certificate reference in protected Helm values.
- [ ] Verify the certificate chain, hostname, renewal ownership, and expiration
  monitoring.

Exit gate:

- DNS resolves to the ingress.
- The certificate is valid for the approved hostname.
- Plain HTTP does not provide an unauthenticated application path.

## Phase 6 - Prepare and review the Helm release

Goal: render the exact release before making cluster changes.

- [ ] Copy the chart defaults to a protected values file outside the repository.
- [ ] Set backend and frontend ACR repositories and digests.
- [ ] Set the mirrored auth-proxy repository and digest.
- [ ] Set `obo.enabled: false`.
- [ ] Populate non-secret Bicep outputs in the protected values file.
- [ ] Reference the approved Secret or Key Vault CSI objects.
- [ ] Configure the hostname and TLS source.
- [ ] Confirm resource requests, limits, HPAs, PDBs, probes, service accounts,
  and NetworkPolicies.
- [ ] Run `helm lint` with the protected values.
- [ ] Render with `helm template` to a protected temporary location.
- [ ] Review the rendered resources for secrets, mutable images, privileged
  containers, host access, broad RBAC, and unintended public services.
- [ ] Delete the rendered manifest after approval.

Exit gate:

- Helm lint and rendering succeed.
- Every image is digest-pinned.
- OBO is absent.
- No Kubernetes Secret value is present in the rendered output.

## Phase 7 - Install the core release

Goal: install the backend, frontend, and auth proxy atomically.

- [ ] Confirm AKS is running.
- [ ] Confirm the operator is using the approved subscription and cluster.
- [ ] Create the namespace through the reviewed Helm operation.
- [ ] Install with `helm upgrade --install --atomic --wait`.
- [ ] Confirm all Deployments become available.
- [ ] Confirm all Pods run as non-root with read-only root filesystems.
- [ ] Confirm Services are ClusterIP and only ingress is externally reachable.
- [ ] Confirm probes, HPAs, PDBs, service accounts, RBAC, and NetworkPolicies are
  present.
- [ ] Record the Helm revision and release image digests outside this public
  plan if they contain environment identifiers.

Exit gate:

- The Helm release is healthy and no rollback was triggered.
- No application endpoint receives user traffic until authentication acceptance
  passes.

## Phase 8 - Core live acceptance

Goal: prove the deployed ARE works and preserves every authorization boundary.

### Platform and authentication

- [ ] Frontend `/healthz` succeeds over TLS.
- [ ] Backend liveness and readiness endpoints succeed.
- [ ] Unauthenticated requests redirect to Entra login or are rejected.
- [ ] Approved users can sign in and sign out.
- [ ] Unassigned or wrong-tenant users are rejected.
- [ ] Auth headers cannot be spoofed by direct backend access.

### Application behavior

- [ ] The frontend loads live backend data.
- [ ] Streaming chat over SSE succeeds.
- [ ] Cancellation and reconnect behavior succeed.
- [ ] Foundry returns a model response.
- [ ] Cosmos stores and reloads conversations.
- [ ] Blob-backed prompt and skill configuration loads.
- [ ] Runtime configuration refresh reaches every backend replica.
- [ ] Conversation ownership prevents cross-user reads and writes.

### Authorization boundaries

- [ ] Azure Monitor and Application Insights access is read-only and bounded.
- [ ] Kubernetes inspection is GET-only and namespace-scoped.
- [ ] Generic KQL, HTTP, Graph, or Kubernetes execution is unavailable.
- [ ] ServiceNow reads succeed.
- [ ] A ServiceNow write requires exact, conversation-bound, expiring,
  single-use approval.
- [ ] The approved write is idempotent, audited, and independently re-read.
- [ ] Reuse, expiry, mismatch, and denial cases cannot mutate ServiceNow.
- [ ] Responses preserve uncertainty, stale-data, partial-result, and
  coverage-gap reporting.

### Observability

- [ ] Application logs, traces, and metrics reach the configured services.
- [ ] Correlation spans the ingress, auth proxy, backend, model call, and
  integration tools.
- [ ] Alerts and workbook queries return expected signals.
- [ ] Logs do not contain tokens, cookies, credentials, or sensitive payloads.

Exit gate:

- All core acceptance scenarios pass with OBO disabled.
- Any failed security or ownership test blocks release.

## Phase 9 - Resilience, recovery, and rollback

Goal: prove the release can tolerate expected disruption and recover safely.

- [ ] Restart one backend pod during an active conversation.
- [ ] Drain or disrupt one node while maintaining service availability.
- [ ] Verify HPA scale-out and scale-in behavior.
- [ ] Verify PDB behavior.
- [ ] Verify readiness prevents premature traffic.
- [ ] Exercise a failed Helm upgrade and automatic rollback.
- [ ] Perform an explicit `helm rollback` to the prior revision.
- [ ] Validate Cosmos point-in-time recovery procedure.
- [ ] Validate Blob version/delete recovery procedure.
- [ ] Rotate one non-production secret and verify rollout behavior.
- [ ] Stop and restart AKS, then verify application recovery.

Exit gate:

- Recovery and rollback procedures are timed, repeatable, and owned.
- No recovery procedure requires enabling local accounts or weakening RBAC.

## Phase 10 - Optional Microsoft Graph OBO

Begin only after the core release passes Phases 1-9.

- [ ] Re-run infrastructure provisioning with `deploy_obo: true`.
- [ ] Configure the approved frontend origin.
- [ ] Grant delegated Graph admin consent with the checked-in operator utility.
- [ ] Add OBO image repository and digest to protected values.
- [ ] Set `obo.enabled: true`.
- [ ] Configure OBO Workload Identity and client values.
- [ ] Render and review the Helm change.
- [ ] Install atomically.
- [ ] Verify delegated user token exchange.
- [ ] Verify mail, calendar, presence, files, and Teams operations remain
  read-only.
- [ ] Verify missing consent, wrong audience, expired token, and unassigned user
  cases fail closed.

Exit gate:

- OBO adds no write path to Microsoft Graph.
- Core ARE operation remains healthy when OBO is unavailable.

## Rollback triggers

Rollback immediately if any of the following occurs:

- Authentication can be bypassed.
- A user can access another user's conversation.
- A technical integration can mutate Azure, Kubernetes, or Microsoft Graph.
- ServiceNow mutates without a valid single-use approval.
- Secrets appear in logs, rendered manifests, or public workflow output.
- Workload Identity falls back to static Azure credentials.
- Images are not the reviewed digests.
- Probes, PDBs, NetworkPolicies, or namespace RBAC are absent.
- Required telemetry is unavailable during acceptance.

## Progress log

### 2026-10-22

- Provisioned and verified the `ktaks` AKS infrastructure through GitHub OIDC.
- Added deployment-principal ACR push permission.
- Registered the deployment service principal in Azure DevOps and granted
  Reader access to `PythonFeed`.
- Built, smoke-tested, scanned, and published backend, frontend, and OBO images.
- Verified all image publication workflow steps passed.
- Deferred OBO infrastructure and runtime enablement to Phase 10.
- Created this deployment plan. Implementation begins with operator access,
  release digests, auth proxy selection, and the pending environment decisions.
- Created the dedicated Entra operator group and added the current operator.
- Added and applied the parameterized AKS RBAC Cluster Admin assignment.
- Verified group-based namespace and Deployment creation authorization through a
  temporary Entra kubeconfig; local AKS accounts remain disabled.
- Resolved all three published application image digests without committing
  registry coordinates.
- Rejected OAuth2 Proxy v7.15.5 because its official image failed the fixable
  HIGH/CRITICAL scan gate.
- Selected Key Vault CSI for application secrets.
- Added separate backend and auth `SecretProviderClass` resources and
  synchronized Kubernetes Secrets.
- Added and deployed a dedicated auth Workload Identity with only Key Vault
  Secrets User, then verified its federation and role assignment.
- Verified both CSI-enabled and existing-Secret Helm render paths.
- Confirmed `AKS_OPERATOR_PRINCIPAL_ID` is configured in the protected GitHub
  `ktaks` environment.
- Confirmed ServiceNow credentials are not ready and live ServiceNow acceptance
  remains blocked.
- Selected Workload Identity and OTLP for Application Insights; no connection
  string secret will be created.
- Replaced the approval-gated Application Routing default-domain plan with the
  GA Azure regional DNS-label path. It uses the existing load balancer and
  requires no purchased domain or Azure DNS zone.
- Selected cert-manager and Let's Encrypt for TLS, with staging issuance before
  production and all cert-manager images scanned, mirrored, and digest-pinned.
- Added the GA endpoint configuration utility, allocated the Azure regional DNS
  label on the existing Application Routing load balancer, and verified public
  DNS resolution without printing or committing the hostname.
- Added a single-owner Let's Encrypt `ClusterIssuer` chart path so the auth and
  application Ingresses share one TLS Secret without competing Certificate
  resources.
- Added optional workflow gates to scan and mirror cert-manager v1.21.3
  controller, webhook, CA injector, ACME solver, and startup check images.
- Verified workflow run `40366741218` passed all five cert-manager image scans
  and mirrored the images to ACR.
- Installed cert-manager v1.21.3 atomically from five protected ACR
  repositories and immutable digests; all deployments became Available.
- Added the required AKS subnet NSG rule for public Application Routing traffic
  after staging issuance identified the organization-created subnet NSG as the
  HTTP timeout boundary.
- Verified public HTTP reachability and successful Let's Encrypt staging
  issuance, then removed the temporary staging Certificate, ClusterIssuer,
  account Secret, and TLS Secret.
- Added the tenant-only confidential login application and deployment service
  principal ownership to Graph/Bicep. The workflow discovers and masks the
  Azure hostname without requiring it as a GitHub secret.
- The first live provisioning attempt created the owned app registration but
  Graph rejected immediate enterprise-application creation while ownership was
  still propagating. Moved that operation to an idempotent post-provision script
  that retries creation, enforces assignment-required access, and assigns the
  operator group.
- The next provisioning run created the assignment-required enterprise
  application, then Graph briefly returned not-found for its relationship
  collection. Added propagation-aware relationship retries.
- Verified workflow run `40474008472` completed successfully. Confirmed live
  that the deployment service principal owns the login application, the
  enterprise application requires assignment, and exactly one assignment
  grants access to `kratos-aks-operators`.
- Added an explicit workflow action that creates a 180-day OAuth credential and
  writes the client ID, client secret, and generated cookie secret through Key
  Vault's ARM resource interface. This preserves the private vault boundary and
  avoids persistent data-plane writer access; live execution remains pending.
- Verified workflow run `39956733655` provisioned the Key Vault CSI changes,
  rebuilt and smoke-tested all application images, passed each image scan, and
  published immutable images for commit `9d0c95a`.
- Stopped AKS while the default-domain preview registration is pending to avoid
  unnecessary cluster compute cost.
- Selected `kratos-aks-operators` as the initial sign-in group for live
  acceptance.
- Selected the existing deployment service principal as the Entra login
  application owner and verified its required Microsoft Graph permissions.
