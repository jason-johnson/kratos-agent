# AKS deployment runbook

This runbook packages the Agent Reliability Engineer for AKS without making
deployment automatic. Provisioning, image publication, DNS changes, certificate
attachment, Helm installation, rollback, and live tests require an operator.

Never commit rendered values, endpoints, IDs, credentials, `.npmrc`, feed URLs,
TLS material, or Kubernetes Secrets. Keep environment-specific values outside
the repository.

## What Phase 5 provides

- `Dockerfile.aks` images for the backend and OBO service, plus the static
  frontend image. Production dependencies and tool versions are pinned.
- An optional AKS platform in the existing Bicep deployment, disabled by
  default with `DEPLOY_AKS=false`.
- Workload Identity federation for the backend and OBO service accounts.
- Least-privilege Azure roles for Cosmos DB, Blob Storage, Foundry, Key Vault,
  Application Insights, and Log Analytics.
- The Azure application-routing add-on, Cilium network policy, Azure Policy,
  Defender monitoring, managed Prometheus, Container Insights, alert rules, and
  an operations workbook.
- A Helm chart with immutable image digests, TLS ingress, an OIDC auth proxy,
  probes, resources, autoscaling, PodDisruptionBudgets, default-deny network
  policy, and same-namespace read-only inspection RBAC.
- Continuous seven-day Cosmos DB backup and 30-day Blob delete/version
  retention. Cosmos and Blob remain managed services; the chart creates no
  stateful data volume.

## Prerequisites

1. Authenticate `az`, `azd`, the private Python feed, and the private npm feed.
2. Configure GitHub repository or organization secrets:
   - `PRIVATE_PYPI_INDEX_URL`
   - `PRIVATE_NPMRC`
3. Prepare a confidential Entra OIDC client for the auth proxy. Its redirect
   URI is `https://<host>/oauth2/callback`.
4. Prepare either:
   - a TLS secret named by `ingress.tlsSecretName`; or
   - a Key Vault certificate and attach the vault to the AKS application-routing
     add-on before setting `ingress.keyVaultCertificateUri`.
5. Prepare the existing Kubernetes Secret named by `secrets.existingSecret`.
   It must contain the keys selected in `values.yaml` for ServiceNow,
   Application Insights, and the auth proxy. Create it from an approved secret
   manager or a protected local env file; do not put values on a command line or
   in a committed values file.

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
azd env set DEPLOY_AKS true
azd env set AKS_NAMESPACE kratos-agent
azd provision
```

`DEPLOY_AKS` defaults to `false`; CI never changes it. The deployment outputs
the cluster name and Workload Identity client IDs for a private Helm values
file.

If Key Vault supplies the ingress certificate, explicitly attach the vault to
the application-routing add-on using the approved target resource IDs:

```bash
az aks approuting update \
  --resource-group <resource-group> \
  --name <cluster> \
  --enable-kv \
  --attach-kv <key-vault-resource-id>
```

Attach an Azure DNS zone separately when managed DNS is required. These are live
cloud mutations and must not be run by an unattended agent.

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

Copy `deploy/helm/kratos-agent/values.yaml` to a protected location outside the
repository. Populate:

- the three application image repositories and digests;
- the auth-proxy image digest;
- Bicep outputs for endpoints, workspace/resource IDs, tenant, and Workload
  Identity client IDs;
- the OBO public client values;
- the ServiceNow origin;
- the public host and TLS source;
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

## 5. Acceptance and rollback

Verify before sending production traffic:

1. all Deployments are available and every Pod is non-root;
2. `/health`, `/health/ready`, `/healthz`, TLS, and OIDC login succeed;
3. unauthenticated and cross-user requests are rejected;
4. Workload Identity can read the configured managed services without keys;
5. Kubernetes tools can read only the release namespace and cannot mutate;
6. ServiceNow, Azure evidence, Graph OBO, and command-brief scenarios pass;
7. telemetry reaches the configured OTLP/Azure Monitor target;
8. the workbook is populated and both alert rules can be exercised safely;
9. HPA, PDB, rolling restart, pod eviction, and one-node disruption preserve
   service; and
10. Cosmos restore and Blob version recovery procedures are tested.

Rollback the Helm release without deleting managed data:

```bash
helm history kratos-agent --namespace kratos-agent
helm rollback kratos-agent <revision> --namespace kratos-agent --wait
```

Resource deletion and migration away from Container Apps/Static Web Apps remain
Phase 6 manual decisions.
