// User-assigned managed identity for the OBO MCP server on AKS.
//
// This single identity plays two roles:
//   1. Is federated to the OBO Kubernetes service account.
//   2. Is the FEDERATED subject for the OBO server app registration — its
//      principalId is the FIC subject, so the app can authenticate to Entra
//      with NO client secret (see obo-entra-app.bicep).

@description('Location')
param location string

@description('Name for the managed identity')
param name string

@description('Tags')
param tags object = {}

resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: name
  location: location
  tags: tags
}

output id string = uami.id
output name string = uami.name
output principalId string = uami.properties.principalId
output clientId string = uami.properties.clientId
