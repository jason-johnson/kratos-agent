@description('Principal ID of the agent service managed identity')
param agentServicePrincipalId string

@description('Cosmos DB account name')
param cosmosDbAccountName string

@description('Microsoft Foundry resource name')
param aiServicesName string

@description('Key Vault name')
param keyVaultName string

@description('Storage Account name for skills blob storage')
param storageAccountName string

@description('Application Insights resource name (for Monitoring Reader RBAC)')
param appInsightsName string = ''

@description('Log Analytics workspace name (for Monitoring Reader RBAC)')
param logAnalyticsName string = ''

@description('Deploying user principal ID')
param principalId string = ''

// ─── Built-in Role Definition IDs ───
var cosmosDbDataContributor = '00000000-0000-0000-0000-000000000002'
var keyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var cognitiveServicesOpenAIUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var monitoringReader = '43d0d8ad-25c7-4714-9337-8ba259a9fe05'

// ─── References ───
resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts@2024-02-15-preview' existing = {
  name: cosmosDbAccountName
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource aiServices 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: aiServicesName
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = if (!empty(appInsightsName)) {
  name: appInsightsName
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = if (!empty(logAnalyticsName)) {
  name: logAnalyticsName
}

// ─── Agent Service → Cosmos DB ───
resource agentCosmosRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-02-15-preview' = {
  parent: cosmosDb
  name: guid(cosmosDb.id, agentServicePrincipalId, cosmosDbDataContributor)
  properties: {
    roleDefinitionId: '${cosmosDb.id}/sqlRoleDefinitions/${cosmosDbDataContributor}'
    principalId: agentServicePrincipalId
    scope: cosmosDb.id
  }
}

// ─── Agent Service → Key Vault ───
resource agentKeyVaultRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, agentServicePrincipalId, keyVaultSecretsUser)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUser)
    principalId: agentServicePrincipalId
    principalType: 'ServicePrincipal'
  }
}

// NOTE: AcrPull is assigned via a User-Assigned Managed Identity in agent-service.bicep
// to avoid the chicken-and-egg problem with the Container App's registry config.

// ─── Agent Service → Microsoft Foundry ───
resource agentAiServicesRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServices.id, agentServicePrincipalId, cognitiveServicesOpenAIUser)
  scope: aiServices
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUser)
    principalId: agentServicePrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ─── Agent Service → Blob Storage (Skills) ───
resource agentStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, agentServicePrincipalId, storageBlobDataContributor)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributor)
    principalId: agentServicePrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ─── Agent Service → Application Insights (Monitoring Reader for Traces panel KQL) ───
resource agentAppInsightsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(appInsightsName)) {
  name: guid(appInsights.id, agentServicePrincipalId, monitoringReader)
  scope: appInsights
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringReader)
    principalId: agentServicePrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource agentLogAnalyticsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(logAnalyticsName)) {
  name: guid(logAnalytics.id, agentServicePrincipalId, monitoringReader)
  scope: logAnalytics
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringReader)
    principalId: agentServicePrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ─── Deploying User → Cosmos DB (for local dev) ───
resource userCosmosRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-02-15-preview' = if (!empty(principalId)) {
  parent: cosmosDb
  name: guid(cosmosDb.id, principalId, cosmosDbDataContributor)
  properties: {
    roleDefinitionId: '${cosmosDb.id}/sqlRoleDefinitions/${cosmosDbDataContributor}'
    principalId: principalId
    scope: cosmosDb.id
  }
}

// ─── Deploying User → Key Vault ───
resource userKeyVaultRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(keyVault.id, principalId, keyVaultSecretsUser)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUser)
    principalId: principalId
    principalType: 'User'
  }
}

// ─── Deploying User → Microsoft Foundry (for local dev) ───
resource userAiServicesRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(aiServices.id, principalId, cognitiveServicesOpenAIUser)
  scope: aiServices
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUser)
    principalId: principalId
    principalType: 'User'
  }
}

// ─── Deploying User → Blob Storage (for local dev) ───
resource userStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(storageAccount.id, principalId, storageBlobDataContributor)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributor)
    principalId: principalId
    principalType: 'User'
  }
}
