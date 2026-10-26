targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the environment (used for resource naming)')
param environmentName string

@minLength(1)
@description('Primary location for all resources')
param location string

@description('Principal ID of the deploying user for role assignments')
param principalId string = ''

// Optional overrides
param containerRegistryName string = ''
param cosmosDbAccountName string = ''
param aiServicesName string = ''
param keyVaultName string = ''
param appInsightsName string = ''
param logAnalyticsName string = ''
param vnetName string = ''
param storageAccountName string = ''

@description('Optional prefix prepended to generated resource names. Empty (default) yields names identical to the base template, so the canonical/prod deployment is unaffected. Set per-subscription (e.g. via AZURE_RESOURCE_PREFIX) to avoid cross-subscription resource name collisions.')
param resourcePrefix string = ''

@description('Deploy the OBO MCP server and its Entra app registrations. Requires directory permission to register Entra applications. Set to false in tenants/subscriptions where the deploying identity cannot create app registrations.')
param deployObo bool = true

@description('Optional AKS cluster name override')
param aksClusterName string = ''

@description('Namespace used by the ARE Helm release')
param aksNamespace string = 'kratos-agent'

@description('Optional Entra group object ID granted AKS RBAC Cluster Admin for manual Helm operations')
param aksOperatorPrincipalId string = ''

@description('Non-overlapping Kubernetes service CIDR')
param aksServiceCidr string = '10.1.0.0/16'

@description('Kubernetes DNS service IP within aksServiceCidr')
param aksDnsServiceIp string = '10.1.0.10'

@description('Non-overlapping Azure CNI Overlay pod CIDR')
param aksPodCidr string = '10.244.0.0/16'

@description('Optional Azure Monitor action group resource ID for AKS alerts')
param aksAlertActionGroupId string = ''

@description('Optional public frontend origin registered as an OBO SPA redirect URI')
param frontendUrl string = ''

@description('Exact OAuth2 Proxy callback URI for the confidential login application')
param authRedirectUri string = ''

@minValue(1)
@description('Foundry model deployment capacity in thousands of tokens per minute')
param foundryModelCapacity int = 250

// ─── Resource Naming ───
var abbrs = loadJsonContent('./abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var namePrefix = empty(resourcePrefix) ? '' : '${resourcePrefix}-'
var namePrefixNoHyphen = empty(resourcePrefix) ? '' : toLower(resourcePrefix)
var tags = { 'azd-env-name': environmentName, project: 'kratos-agent' }

// ─── Resource Group ───
resource rg 'Microsoft.Resources/resourceGroups@2022-09-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

// ─── Networking ───
module network './modules/network.bicep' = {
  name: 'network'
  scope: rg
  params: {
    name: !empty(vnetName) ? vnetName : '${namePrefix}${abbrs.networkVirtualNetworks}${resourceToken}'
    location: location
    tags: tags
  }
}

// ─── Log Analytics ───
module logAnalytics './modules/log-analytics.bicep' = {
  name: 'log-analytics'
  scope: rg
  params: {
    name: !empty(logAnalyticsName) ? logAnalyticsName : '${namePrefix}${abbrs.operationalInsightsWorkspaces}${resourceToken}'
    location: location
    tags: tags
  }
}

resource subscriptionActivityLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: '${namePrefix}administrative-activity-${resourceToken}'
  scope: subscription()
  properties: {
    workspaceId: logAnalytics.outputs.id
    logs: [
      {
        category: 'Administrative'
        enabled: true
      }
      {
        category: 'ServiceHealth'
        enabled: true
      }
      {
        category: 'ResourceHealth'
        enabled: true
      }
    ]
  }
}

// ─── Application Insights ───
module appInsights './modules/app-insights.bicep' = {
  name: 'app-insights'
  scope: rg
  params: {
    name: !empty(appInsightsName) ? appInsightsName : '${namePrefix}${abbrs.insightsComponents}${resourceToken}'
    location: location
    tags: tags
    logAnalyticsWorkspaceId: logAnalytics.outputs.id
  }
}

// ─── Key Vault ───
module keyVault './modules/key-vault.bicep' = {
  name: 'key-vault'
  scope: rg
  params: {
    name: !empty(keyVaultName) ? keyVaultName : '${namePrefix}${abbrs.keyVaultVaults}${resourceToken}'
    location: location
    tags: tags
    subnetId: network.outputs.privateEndpointSubnetId
    vnetId: network.outputs.id
  }
}

// ─── Cosmos DB ───
module cosmosDb './modules/cosmos-db.bicep' = {
  name: 'cosmos-db'
  scope: rg
  params: {
    name: !empty(cosmosDbAccountName) ? cosmosDbAccountName : '${namePrefix}${abbrs.documentDBDatabaseAccounts}${resourceToken}'
    location: location
    tags: tags
    subnetId: network.outputs.privateEndpointSubnetId
    vnetId: network.outputs.id
  }
}

// ─── Microsoft Foundry ───
module aiFoundry './modules/ai-services.bicep' = {
  name: 'ai-foundry'
  scope: rg
  params: {
    name: !empty(aiServicesName) ? aiServicesName : '${namePrefix}${abbrs.cognitiveServicesAccounts}${resourceToken}'
    location: location
    tags: tags
    appInsightsId: appInsights.outputs.id
    appInsightsConnectionString: appInsights.outputs.connectionString
    modelCapacity: foundryModelCapacity
  }
}

// ─── Blob Storage (Skills) ───
module blobStorage './modules/blob-storage.bicep' = {
  name: 'blob-storage'
  scope: rg
  params: {
    name: !empty(storageAccountName) ? storageAccountName : '${namePrefixNoHyphen}${abbrs.storageAccounts}${resourceToken}'
    location: location
    tags: tags
    subnetId: network.outputs.privateEndpointSubnetId
    vnetId: network.outputs.id
  }
}

// ─── Container Registry ───
module containerRegistry './modules/container-registry.bicep' = {
  name: 'container-registry'
  scope: rg
  params: {
    name: !empty(containerRegistryName) ? containerRegistryName : '${namePrefixNoHyphen}${abbrs.containerRegistryRegistries}${resourceToken}'
    location: location
    tags: tags
  }
}

// ─── OBO MCP Server: identity and Entra apps ───
// The user-assigned identity is federated to the AKS service account below and
// is also the subject trusted by the OBO server app registration.
module oboIdentity './modules/obo-identity.bicep' = if (deployObo) {
  name: 'obo-identity'
  scope: rg
  params: {
    name: 'id-obo-${resourceToken}'
    location: location
    tags: tags
  }
}

// SPA client app (MSAL.js signs the user in here). Deployed first so its appId
// can pre-authorize the server app, avoiding a circular dependency.
module oboEntraAppClient './modules/obo-entra-app.bicep' = if (deployObo) {
  name: 'obo-entra-app-client'
  scope: rg
  params: {
    entraAppDisplayName: 'kratos-obo-client-${environmentName}'
    entraAppUniqueName: 'kratos-obo-client-${resourceToken}'
    isServer: false
    spaRedirectUris: concat(
      empty(frontendUrl) ? [] : [frontendUrl],
      [
        'http://localhost:3000'
        'http://localhost:5173'
      ]
    )
  }
}

// OBO server app (the MCP server's Entra identity + Graph User.Read + FIC).
module oboEntraAppServer './modules/obo-entra-app.bicep' = if (deployObo) {
  name: 'obo-entra-app-server'
  scope: rg
  params: {
    entraAppDisplayName: 'kratos-obo-server-${environmentName}'
    entraAppUniqueName: 'kratos-obo-server-${resourceToken}'
    isServer: true
    knownClientAppId: oboEntraAppClient!.outputs.entraAppClientId
    managedIdentityObjectId: oboIdentity!.outputs.principalId
  }
}

module authEntraApp './modules/auth-entra-app.bicep' = if (!empty(authRedirectUri)) {
  name: 'auth-entra-app'
  scope: rg
  params: {
    displayName: 'kratos-login-${environmentName}'
    uniqueName: 'kratos-login-${resourceToken}'
    redirectUri: authRedirectUri
    ownerPrincipalId: principalId
    allowedGroupId: aksOperatorPrincipalId
  }
}

module aksPlatform './modules/aks-platform.bicep' = {
  name: 'aks-platform'
  scope: rg
  params: {
    name: !empty(aksClusterName) ? aksClusterName : '${namePrefix}aks-${resourceToken}'
    location: location
    tags: tags
    subnetId: network.outputs.aksSubnetId
    vnetName: network.outputs.name
    serviceCidr: aksServiceCidr
    dnsServiceIp: aksDnsServiceIp
    podCidr: aksPodCidr
    logAnalyticsWorkspaceId: logAnalytics.outputs.id
    containerRegistryName: containerRegistry.outputs.name
    cosmosDbAccountName: cosmosDb.outputs.name
    aiServicesName: aiFoundry.outputs.name
    keyVaultName: keyVault.outputs.name
    storageAccountName: blobStorage.outputs.name
    appInsightsName: appInsights.outputs.name
    logAnalyticsName: logAnalytics.outputs.name
    kubernetesNamespace: aksNamespace
    oboIdentityName: deployObo ? oboIdentity!.outputs.name : ''
    deployObo: deployObo
    alertActionGroupId: aksAlertActionGroupId
    clusterAdminPrincipalId: principalId
    operatorPrincipalId: aksOperatorPrincipalId
    imagePublisherPrincipalId: principalId
  }
}

// ─── Outputs ───
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_CONTAINER_REGISTRY_NAME string = containerRegistry.outputs.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerRegistry.outputs.loginServer
output AZURE_COSMOS_DB_ENDPOINT string = cosmosDb.outputs.endpoint
output AZURE_KEY_VAULT_URI string = keyVault.outputs.uri
output AZURE_APP_INSIGHTS_CONNECTION_STRING string = appInsights.outputs.connectionString
output APPLICATION_INSIGHTS_RESOURCE_ID string = appInsights.outputs.id
output APPLICATION_INSIGHTS_WORKSPACE_ID string = logAnalytics.outputs.customerId
output FOUNDRY_ENDPOINT string = aiFoundry.outputs.endpoint
output FOUNDRY_MODEL_DEPLOYMENT string = aiFoundry.outputs.modelDeploymentName
output AZURE_BLOB_STORAGE_ENDPOINT string = blobStorage.outputs.endpoint
output AZURE_BLOB_STORAGE_ACCOUNT_NAME string = blobStorage.outputs.name
output AKS_CLUSTER_NAME string = aksPlatform.outputs.clusterName
output AKS_BACKEND_IDENTITY_CLIENT_ID string = aksPlatform.outputs.backendIdentityClientId
output AKS_AUTH_IDENTITY_CLIENT_ID string = aksPlatform.outputs.authIdentityClientId
output AKS_OBO_IDENTITY_CLIENT_ID string = deployObo ? aksPlatform.outputs.oboIdentityClientId : ''
output AKS_OIDC_ISSUER_URL string = aksPlatform.outputs.oidcIssuerUrl
output ENTRA_AUTH_CLIENT_ID string = !empty(authRedirectUri) ? authEntraApp!.outputs.clientId : ''
output AKS_OPERATIONS_WORKBOOK_ID string = aksPlatform.outputs.operationsWorkbookId

// ─── OBO MCP server outputs ───
output OBO_SERVER_APP_CLIENT_ID string = deployObo ? oboEntraAppServer!.outputs.entraAppClientId : ''
output OBO_SERVER_APP_IDENTIFIER_URI string = deployObo ? oboEntraAppServer!.outputs.entraAppIdentifierUri : ''
output OBO_SERVER_APP_SCOPE_VALUE string = deployObo ? oboEntraAppServer!.outputs.entraAppScopeValue : ''
output OBO_CLIENT_APP_CLIENT_ID string = deployObo ? oboEntraAppClient!.outputs.entraAppClientId : ''
output OBO_TENANT_ID string = tenant().tenantId
