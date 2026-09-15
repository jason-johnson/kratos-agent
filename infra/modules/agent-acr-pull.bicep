@description('Object IDs of the current Foundry hosted-agent runtime identities.')
param agentPrincipalIds array

@description('Container Registry name that stores the hosted-agent image')
param containerRegistryName string

var acrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: containerRegistryName
}

resource agentAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principalId in agentPrincipalIds: {
  name: guid(containerRegistry.id, principalId, acrPull)
  scope: containerRegistry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPull)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}]
