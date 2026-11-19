# AKS deployment runbook

This runbook packages the Agent Reliability Engineer for AKS without making
deployment automatic. Provisioning, image publication, DNS changes, certificate
attachment, Helm installation, rollback, and live tests require an operator.

Never commit rendered values, endpoints, IDs, credentials, `.npmrc`, feed URLs,
TLS material, or Kubernetes Secrets. Keep environment-specific values outside
the repository.

## Supported architecture

- `Dockerfile.aks` images for the backend and OBO service, plus the static
  frontend image. Production dependencies and tool versions are pinned.
- An AKS-only Bicep deployment. Application deployment remains a separate,
  manual Helm operation.
- Workload Identity federation for the backend and OBO service accounts.
- Least-privilege Azure roles for Cosmos DB, Blob Storage, Foundry, Key Vault,
  Application Insights, and Log Analytics.
- The Azure application-routing add-on, Cilium network policy, Azure Policy,
  Defender monitoring, managed Prometheus, Container Insights, alert rules, and
  an operations workbook.
- A Helm chart with immutable image digests, TLS ingress, an OIDC auth proxy,
  probes, resources, autoscaling, PodDisruptionBudgets, default-deny network
  policy, and same-namespace read-only inspection RBAC.

The AKS backend image contains the Copilot CLI but not the synthetic local
Microsoft 365 Graph MCP package. Production sessions filter that known local
mock command; Microsoft Graph is available only through the separately enabled
OBO service.
- Continuous seven-day Cosmos DB backup and 30-day Blob delete/version
  retention. Cosmos and Blob remain managed services; the chart creates no
  stateful data volume.

## Prerequisites

1. Authenticate `az`, `azd`, the private Python feed, and the private npm feed.
2. Configure GitHub repository or organization secrets:
   - `PRIVATE_PYPI_INDEX_URL`
   - `PRIVATE_NPMRC`
3. Configure the Azure-provided regional DNS label described below. Prepare a
   confidential Entra OIDC client for the auth proxy only after the hostname is
   allocated. Its redirect URI is `https://<host>/oauth2/callback`.
4. Install the pinned cert-manager release and configure the chart's Let's
   Encrypt issuer. A pre-existing TLS Secret or Key Vault certificate remains
   supported as an alternative.
5. Prefer Key Vault CSI by setting `secrets.keyVaultCsi.enabled` and supplying:
   - the Key Vault name;
   - the ServiceNow client ID and client secret object names;
   - the OAuth2 Proxy client ID, client secret, and cookie secret object names;
   - the optional Application Insights connection-string object name.
   Bicep creates separate backend and auth Workload Identities. The auth identity
   receives only Key Vault Secrets User, while the backend retains its existing
   managed-service roles. The chart synchronizes separate backend and auth
   Kubernetes Secrets and mounts each `SecretProviderClass` only into its owning
   workload.

   Set `serviceNow.enabled: false` when the initial release does not integrate
   with ServiceNow. In that mode the chart does not require its origin or secret
   object names, and the backend excludes all ServiceNow tools and the
   incident-update skill. Do not use placeholder credentials.

   If Key Vault CSI is deliberately disabled, prepare the existing Kubernetes
   Secret named by `secrets.existingSecret`. It must contain the keys selected in
   `values.yaml` for ServiceNow, Application Insights, and the auth proxy. Create
   it from an approved secret manager or a protected local env file; do not put
   values on a command line or in a committed values file.

## 1. Validate without deploying

```bash
helm lint deploy/helm/kratos-agent \
  -f deploy/helm/kratos-agent/values-ci.yaml

helm template validation deploy/helm/kratos-agent \
  --namespace kratos-agent \
  -f deploy/helm/kratos-agent/values-ci.yaml > rendered.yaml

az bicep build --file infra/main.bicep --stdout > /dev/null
```

`values-ci.yaml` contains synthetic validation values only and is not
deployable.

## 2. Provision AKS manually

Select the intended non-production azd environment and review it before
changing anything:

```bash
azd env get-value AZURE_ENV_NAME
azd env set AKS_NAMESPACE kratos-agent
azd env set FRONTEND_URL https://<host>
azd provision
```

### Optional GitHub OIDC provisioning trial

The manual `Provision AKS infrastructure` workflow provisions the fixed
non-production `ktaks` environment from a GitHub-hosted runner. It always runs
`azd provision` and can optionally build, smoke-test, and publish the three
application images when `publish_images` is selected. Helm installation, DNS,
TLS, secrets, and live acceptance remain separate operator actions.

Create a GitHub environment named `ktaks` with these secrets:

- `AZURE_CLIENT_ID` - application/client ID for the deployment service
  principal;
- `AZURE_TENANT_ID` - its tenant ID; and
- `AZURE_SUBSCRIPTION_ID` - the approved target subscription; and
- `AKS_OPERATOR_PRINCIPAL_ID` - object ID of the approved Entra operator group
  used for assigned-user access.

Add this `ktaks` environment variable:

- `PRIVATE_PYPI_INDEX_URL` - the uncredentialed HTTPS index URL for the approved
  private Python feed. The workflow injects its short-lived Azure DevOps token
  at runtime; do not include credentials in this variable.
  granted AKS RBAC Cluster Admin for manual Helm operations.

Set its `AZURE_LOCATION` environment variable to `swedencentral`.
The `ktaks` workflow requests 250 thousand tokens per minute for the Foundry
model deployment, matching the approved regional quota.

Add an OIDC federated credential to the deployment application with:

- issuer `https://token.actions.githubusercontent.com`;
- audience `api://AzureADTokenExchange`; and
- subject `repo:jason-johnson/kratos-agent:environment:ktaks`.

The workflow is manual-only and defaults `deploy_obo` to false. Enable OBO
only after confirming the deployment identity may create Entra applications.
The repository is public, so review workflow logs for environment disclosure
and never print azd values, resource IDs, endpoints, or credentials.

The deployment outputs the cluster name and backend, auth, and optional OBO
Workload Identity client IDs plus the confidential login application's client
ID for a private Helm values file. The deployment service principal owns the
login application, sign-in requires assignment, and the operator group is
assigned through Microsoft Graph. Because Graph can take time to recognize new
application ownership, the workflow creates the app registration through Bicep
and then idempotently retries enterprise-application creation and group
assignment with `deploy/aks/scripts/configure-auth-enterprise-app.sh`. After the
Azure regional hostname exists, the workflow discovers it from the AKS managed
resource group, masks it, and configures the exact OAuth callback only in the
ephemeral azd environment.

After the login application and Key Vault exist, rerun the manual workflow with
`configure_auth_secrets` selected. The deployment service principal creates a
180-day OAuth credential and stores the client ID, client secret, and generated
cookie secret through Key Vault's ARM resource interface. This does not require
public vault access or a persistent Key Vault data-plane writer. The operation
is idempotent and removes the newly created application password if vault
storage fails.

The stable Key Vault object names are:

- `kratos-oauth-client-id`
- `kratos-oauth-client-secret`
- `kratos-oauth-cookie-secret`

The official OAuth2 Proxy v7.15.5 image is not deployed directly because its Go
binary fails the fixable HIGH/CRITICAL release gate. Select
`publish_auth_proxy_image` in the manual workflow to reproducibly rebuild the
exact v7.15.5 release commit with the patched Go 1.26.10 toolchain and the
minimal `golang.org/x/net` 0.60.0 security update. The workflow verifies the
source archive checksum, uses digest-pinned builder and distroless runtime
images, verifies the resulting binary identity, applies the standard Trivy
gate, and publishes the approved result to the private
`platform/oauth2-proxy` ACR repository.
`FRONTEND_URL`
registers the production origin on the OBO SPA client; omit it only when
provisioning a local-only environment.

For an environment created by the former Container Apps template, decommission
the live Container Apps resources before applying this template. Removing the
delegated Container Apps subnet from a VNet still in use will fail safely.

If Key Vault supplies the ingress certificate, explicitly attach the vault to
the application-routing add-on using the approved target resource IDs:

```bash
az aks approuting update \
  --resource-group <resource-group> \
  --name <cluster> \
  --enable-kv \
  --attach-kv <key-vault-resource-id>
```

### Azure-provided public hostname

The supported endpoint uses the managed Gateway API load balancer and Azure's
regional `cloudapp.azure.com` DNS service. It requires no purchased domain,
Azure DNS zone, or preview feature. Configure `gateway.azureDnsLabel` and
`gateway.host` only in protected Helm values; do not commit either value.

Do not enable the approval-gated Application Routing default-domain preview.
The regional Azure DNS label is generally available.

If OBO is enabled, an Entra administrator must grant the requested delegated
Graph permissions after provisioning:

```bash
./deploy/aks/scripts/grant-obo-consent.sh
```

On Windows, run `./deploy/aks/scripts/grant-obo-consent.ps1`. The utilities use
the selected azd environment output unless `OBO_SERVER_APP_CLIENT_ID` is already
set, are idempotent, and fail non-zero when consent is incomplete.

## 3. Build and publish immutable images

The AKS Dockerfiles require BuildKit secrets. They do not fall back to public
package feeds and do not copy feed credentials into image layers.

```bash
export DOCKER_BUILDKIT=1

docker build \
  --secret id=pip_index_url,env=PRIVATE_PYPI_INDEX_URL \
  --secret id=npmrc,src="$HOME/.npmrc" \
  -f src/backend/Dockerfile.aks \
  -t <registry>/kratos-agent:<commit> .

docker build \
  --secret id=npmrc,src="$HOME/.npmrc" \
  -f src/frontend/Dockerfile \
  -t <registry>/kratos-frontend:<commit> src/frontend

docker build \
  --secret id=pip_index_url,env=PRIVATE_PYPI_INDEX_URL \
  -f src/obo-mcp-server/Dockerfile.aks \
  -t <registry>/kratos-obo:<commit> src/obo-mcp-server
```

Scan all three images before pushing. After an operator pushes them, resolve
their registry digests and place `repository` plus `sha256:...` in the private
values file. The chart rejects mutable tag-only deployment.

## 4. Configure and install the chart

### Install cert-manager

The manual workflow can scan and mirror cert-manager v1.21.3 by selecting
`publish_platform_images`. All five images must pass the same fixable
HIGH/CRITICAL gate as the application images. After the workflow succeeds,
resolve each ACR repository and digest privately and create a protected
cert-manager values file:

```yaml
crds:
  enabled: true
config:
  gatewayAPI:
    enabled: true
image:
  repository: <acr>/platform/cert-manager-controller
  digest: sha256:<digest>
webhook:
  image:
    repository: <acr>/platform/cert-manager-webhook
    digest: sha256:<digest>
cainjector:
  image:
    repository: <acr>/platform/cert-manager-cainjector
    digest: sha256:<digest>
acmesolver:
  image:
    repository: <acr>/platform/cert-manager-acmesolver
    digest: sha256:<digest>
startupapicheck:
  image:
    repository: <acr>/platform/cert-manager-startupapicheck
    digest: sha256:<digest>
```

Install the official chart at its immutable chart version:

```bash
helm upgrade --install cert-manager \
  oci://quay.io/jetstack/charts/cert-manager \
  --version v1.21.3 \
  --namespace cert-manager \
  --create-namespace \
  --values <protected-cert-manager-values.yaml> \
  --atomic \
  --wait \
  --timeout 10m
```

Test issuance with `gateway.letsEncrypt.environment: staging` first. After the
staging Certificate is ready, switch to `production` with distinct issuer,
account Secret, and TLS Secret names. Verify production issuance before
deleting only the unreferenced staging resources. The chart creates one
`ClusterIssuer`; cert-manager's Gateway integration requests the TLS certificate.

Copy `deploy/helm/kratos-agent/values.yaml` to a protected location outside the
repository. Populate:

- the three application image repositories and digests;
- the auth-proxy image digest;
- Bicep outputs for endpoints, workspace/resource IDs, tenant, and Workload
  Identity client IDs;
- Key Vault CSI object names when CSI is enabled;
- the OBO public client values only when OBO is enabled;
- the ServiceNow origin only when ServiceNow is enabled;
- the protected public host, Let's Encrypt issuer name, environment, account
  email, account Secret name, and TLS Secret name;
- an optional Azure Monitor action group during provisioning.

Create the namespace and secret through the approved secret-management process,
then render and inspect the exact manifests:

```bash
helm template kratos-agent deploy/helm/kratos-agent \
  --namespace kratos-agent \
  --values <private-values.yaml> > <reviewed-render.yaml>
```

Only after review, install manually:

```bash
helm upgrade --install kratos-agent deploy/helm/kratos-agent \
  --namespace kratos-agent \
  --create-namespace \
  --values <private-values.yaml> \
  --atomic \
  --wait \
  --timeout 15m
```

### Gateway-only operating baseline

The managed NGINX migration is complete. Revision 23 passed final authenticated
acceptance on 2026-11-17; the legacy controller, public IP, callback, and TLS
resources were removed. The following is the repeatable Gateway setup, not an
outstanding cutover checklist:

1. Provision the managed standard Gateway API CRDs and the `approuting-istio`
   implementation through Bicep.
   Restart cert-manager after the managed CRDs are ready because cert-manager
   detects Gateway API support only during startup.
2. Choose a new Azure regional DNS label and corresponding
   `cloudapp.azure.com` hostname. Keep both values outside Git.
3. Set `gateway.enabled`, `gateway.azureDnsLabel`, `gateway.host`,
   `gateway.clusterIssuer`, and `gateway.letsEncrypt` in the protected Helm
   values.
4. Install the chart atomically. The Gateway routes only to OAuth2 Proxy;
   OAuth2 Proxy routes `/api` and `/health` to the backend and all other paths
   to the frontend while supplying trusted identity headers. The default-deny
   policy permits generated Gateway proxies to reach the managed control plane
   only in `gateway.controlPlaneNamespace` on TCP 15012 and exposes only their
   public HTTP/HTTPS ports. Proxy backend egress is limited to OAuth2 Proxy on
   TCP 4180 and cert-manager HTTP-01 solver pods on TCP 8089.
5. Rerun the manual infrastructure workflow after the Gateway load balancer
   exists so the confidential Entra application contains its callback URI.
6. Exercise TLS, assigned and unassigned login, admin authorization, SSE,
   user-input responses, cancellation, refresh/reconnect, pod replacement, and
   rollback against the new hostname.
7. Keep the AKS default NGINX controller type at `None`. Keep Web Application
   Routing enabled because it owns the `approuting-istio` implementation. Do not
   restore a legacy callback, Ingress, or controller as a routine Helm rollback.

Do not commit hostnames, rendered manifests, callback URIs, or protected
Gateway values.

## 5. Acceptance and rollback

Verify before sending production traffic:

1. all Deployments are available and every Pod is non-root;
2. `/health`, `/health/ready`, `/healthz`, TLS, and OIDC login succeed;
3. unauthenticated and cross-user requests are rejected;
4. Workload Identity can read the configured managed services without keys;
5. Kubernetes tools can read only the release namespace and cannot mutate;
6. Azure evidence and command-brief scenarios pass; ServiceNow and Graph OBO
   acceptance is required only before enabling those integrations;
7. telemetry reaches the configured OTLP/Azure Monitor target;
8. the workbook is populated and both alert rules can be exercised safely;
9. HPA, PDB, rolling restart, pod eviction, and one-node disruption preserve
   service; and
10. Cosmos restore and Blob version recovery procedures are tested.

Core functional, Gateway authentication, cancellation, pod replacement, and Helm
rollback acceptance is already recorded in
[DEPLOYMENT-PLAN.md](../../DEPLOYMENT-PLAN.md). Its reconciled checklist separates
that evidence from outstanding security, observability, scaling, secret-rotation,
and data-recovery drills; this runbook is not evidence that every drill passed.

Rollback the Helm release without deleting managed data:

```bash
helm history kratos-agent --namespace kratos-agent
helm rollback kratos-agent <revision> --namespace kratos-agent --wait
```

Choose a reviewed Gateway-only revision compatible with the current
infrastructure. Historical dual-ingress revisions are not immediate rollback
targets now that the NGINX controller, callback, and TLS resources are gone.

Deleting retired cloud resources and managed data is always a separate,
explicit operator decision.
