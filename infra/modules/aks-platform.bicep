@description('AKS cluster name')
param name string

@description('Azure region')
param location string

@description('Resource tags')
param tags object = {}

@description('AKS node subnet resource ID')
param subnetId string

@description('Virtual network name containing the AKS subnet')
param vnetName string

@description('Kubernetes service CIDR that does not overlap the VNet')
param serviceCidr string

@description('Kubernetes DNS service IP within serviceCidr')
param dnsServiceIp string

@description('Azure CNI Overlay pod CIDR that does not overlap the VNet or service CIDR')
param podCidr string

@description('Log Analytics workspace resource ID')
param logAnalyticsWorkspaceId string

@description('Container registry name')
param containerRegistryName string

@description('Cosmos DB account name')
param cosmosDbAccountName string

@description('Microsoft Foundry account name')
param aiServicesName string

@description('Key Vault name')
param keyVaultName string

@description('Skills storage account name')
param storageAccountName string

@description('Application Insights component name')
param appInsightsName string

@description('Log Analytics workspace name')
param logAnalyticsName string

@description('Kubernetes namespace hosting the ARE')
param kubernetesNamespace string = 'kratos-agent'

@description('Backend Kubernetes service account name')
param backendServiceAccountName string = 'kratos-agent-backend'

@description('OBO Kubernetes service account name')
param oboServiceAccountName string = 'kratos-agent-obo'

@description('Existing OBO user-assigned identity name')
param oboIdentityName string = ''

@description('Provision OBO Workload Identity federation')
param deployObo bool = true

@description('System node VM size')
param nodeVmSize string = 'Standard_D4ds_v5'

@description('Optional Azure Monitor action group resource ID for AKS alerts')
param alertActionGroupId string = ''

@description('Principal granted Azure Kubernetes Service RBAC Cluster Admin for initial operation')
param clusterAdminPrincipalId string = ''

@description('Principal granted permission to push immutable application images to ACR')
param imagePublisherPrincipalId string = ''

var backendIdentityName = '${name}-backend'
var cosmosDbDataContributor = '00000000-0000-0000-0000-000000000002'
var keyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var cognitiveServicesOpenAIUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var monitoringReader = '43d0d8ad-25c7-4714-9337-8ba259a9fe05'
var acrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var acrPush = '8311e382-0749-4cb8-b61a-304f252e45ec'
var networkContributor = '4d97b98b-1d4f-4787-a291-c67834d212e7'
var aksRbacClusterAdmin = 'b1ff04bb-8a4e-4dc4-8eb5-8693973ce19b'

resource backendIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: backendIdentityName
  location: location
  tags: tags
}

resource oboIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = if (deployObo) {
  name: oboIdentityName
}

resource aks 'Microsoft.ContainerService/managedClusters@2024-09-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    dnsPrefix: name
    enableRBAC: true
    disableLocalAccounts: true
    nodeResourceGroup: '${name}-nodes'
    aadProfile: {
      managed: true
      enableAzureRBAC: true
      tenantID: tenant().tenantId
    }
    agentPoolProfiles: [
      {
        name: 'system'
        count: 3
        vmSize: nodeVmSize
        osType: 'Linux'
        osSKU: 'AzureLinux'
        mode: 'System'
        type: 'VirtualMachineScaleSets'
        enableAutoScaling: true
        minCount: 3
        maxCount: 6
        maxPods: 50
        vnetSubnetID: subnetId
        availabilityZones: [
          '1'
          '2'
          '3'
        ]
        upgradeSettings: {
          maxSurge: '33%'
          drainTimeoutInMinutes: 30
          nodeSoakDurationInMinutes: 5
        }
      }
    ]
    oidcIssuerProfile: {
      enabled: true
    }
    securityProfile: {
      workloadIdentity: {
        enabled: true
      }
      imageCleaner: {
        enabled: true
        intervalHours: 168
      }
      defender: {
        securityMonitoring: {
          enabled: true
        }
        logAnalyticsWorkspaceResourceId: logAnalyticsWorkspaceId
      }
    }
    addonProfiles: {
      azureKeyvaultSecretsProvider: {
        enabled: true
        config: {
          enableSecretRotation: 'true'
          rotationPollInterval: '2m'
        }
      }
      azurepolicy: {
        enabled: true
      }
      omsagent: {
        enabled: true
        config: {
          logAnalyticsWorkspaceResourceID: logAnalyticsWorkspaceId
          useAADAuth: 'true'
        }
      }
    }
    azureMonitorProfile: {
      metrics: {
        enabled: true
      }
    }
    ingressProfile: {
      webAppRouting: {
        enabled: true
      }
    }
    networkProfile: {
      networkPlugin: 'azure'
      networkPluginMode: 'overlay'
      networkDataplane: 'cilium'
      networkPolicy: 'cilium'
      loadBalancerSku: 'standard'
      outboundType: 'loadBalancer'
      serviceCidr: serviceCidr
      dnsServiceIP: dnsServiceIp
      podCidr: podCidr
    }
    autoUpgradeProfile: {
      upgradeChannel: 'stable'
      nodeOSUpgradeChannel: 'NodeImage'
    }
    storageProfile: {
      diskCSIDriver: {
        enabled: true
      }
      fileCSIDriver: {
        enabled: true
      }
      snapshotController: {
        enabled: true
      }
    }
  }
}

resource backendFederatedCredential 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: backendIdentity
  name: 'aks-${kubernetesNamespace}-${backendServiceAccountName}'
  properties: {
    audiences: ['api://AzureADTokenExchange']
    issuer: aks.properties.oidcIssuerProfile.issuerURL
    subject: 'system:serviceaccount:${kubernetesNamespace}:${backendServiceAccountName}'
  }
}

resource oboFederatedCredential 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = if (deployObo) {
  parent: oboIdentity
  name: 'aks-${kubernetesNamespace}-${oboServiceAccountName}'
  properties: {
    audiences: ['api://AzureADTokenExchange']
    issuer: aks.properties.oidcIssuerProfile.issuerURL
    subject: 'system:serviceaccount:${kubernetesNamespace}:${oboServiceAccountName}'
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: containerRegistryName
}

resource vnet 'Microsoft.Network/virtualNetworks@2023-09-01' existing = {
  name: vnetName
}

resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: cosmosDbAccountName
}

resource aiServices 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: aiServicesName
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsName
}

resource kubeletAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, aks.name, acrPull)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPull)
    principalId: aks.properties.identityProfile.kubeletidentity.objectId
    principalType: 'ServicePrincipal'
  }
}

resource imagePublisherAcrPush 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(imagePublisherPrincipalId)) {
  name: guid(registry.id, imagePublisherPrincipalId, acrPush)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPush)
    principalId: imagePublisherPrincipalId
  }
}

resource controlPlaneNetworkRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vnet.id, aks.name, networkContributor)
  scope: vnet
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', networkContributor)
    principalId: aks.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource clusterAdminRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(clusterAdminPrincipalId)) {
  name: guid(aks.id, clusterAdminPrincipalId, aksRbacClusterAdmin)
  scope: aks
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', aksRbacClusterAdmin)
    principalId: clusterAdminPrincipalId
  }
}

resource backendCosmosRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmosDb
  name: guid(cosmosDb.id, backendIdentity.name, cosmosDbDataContributor)
  properties: {
    roleDefinitionId: '${cosmosDb.id}/sqlRoleDefinitions/${cosmosDbDataContributor}'
    principalId: backendIdentity.properties.principalId
    scope: cosmosDb.id
  }
}

resource backendKeyVaultRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, backendIdentity.name, keyVaultSecretsUser)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUser)
    principalId: backendIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource backendAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServices.id, backendIdentity.name, cognitiveServicesOpenAIUser)
  scope: aiServices
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUser)
    principalId: backendIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource backendStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, backendIdentity.name, storageBlobDataContributor)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributor)
    principalId: backendIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource backendAppInsightsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appInsights.id, backendIdentity.name, monitoringReader)
  scope: appInsights
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringReader)
    principalId: backendIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource backendLogAnalyticsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(logAnalytics.id, backendIdentity.name, monitoringReader)
  scope: logAnalytics
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringReader)
    principalId: backendIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

var workbookData = {
  version: 'Notebook/1.0'
  items: [
    {
      type: 1
      content: {
        json: '# Agent Reliability Engineer on AKS\nHealth, restarts, readiness, and recent backend errors for the ARE namespace.'
      }
      name: 'overview'
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'KubePodInventory | where Namespace == "${kubernetesNamespace}" | summarize Pods=dcount(PodUid), Ready=countif(PodStatus == "Running"), NotReady=countif(PodStatus != "Running") by bin(TimeGenerated, 5m) | render timechart'
        size: 0
        title: 'Pod readiness'
        queryType: 0
        resourceType: 'microsoft.operationalinsights/workspaces'
        crossComponentResources: [
          logAnalyticsWorkspaceId
        ]
      }
      name: 'readiness'
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'KubePodInventory | where Namespace == "${kubernetesNamespace}" | summarize Restarts=max(ContainerRestartCount) by Name, bin(TimeGenerated, 10m) | render timechart'
        size: 0
        title: 'Container restarts'
        queryType: 0
        resourceType: 'microsoft.operationalinsights/workspaces'
        crossComponentResources: [
          logAnalyticsWorkspaceId
        ]
      }
      name: 'restarts'
    }
  ]
  fallbackResourceIds: [
    aks.id
  ]
}

resource operationsWorkbook 'Microsoft.Insights/workbooks@2022-04-01' = {
  name: guid(aks.id, 'operations-workbook')
  location: location
  kind: 'shared'
  tags: tags
  properties: {
    displayName: '${name} operations'
    category: 'workbook'
    sourceId: aks.id
    serializedData: string(workbookData)
    version: '1.0'
  }
}

resource unavailablePodsAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: '${name}-pods-unavailable'
  location: location
  tags: tags
  properties: {
    displayName: '${name}: ARE pods unavailable'
    description: 'One or more ARE pods have remained outside Running state.'
    enabled: true
    severity: 1
    evaluationFrequency: 'PT5M'
    windowSize: 'PT10M'
    scopes: [
      logAnalyticsWorkspaceId
    ]
    targetResourceTypes: [
      'Microsoft.ContainerService/managedClusters'
    ]
    criteria: {
      allOf: [
        {
          query: 'KubePodInventory | where Namespace == "${kubernetesNamespace}" | summarize arg_max(TimeGenerated, *) by PodUid | where PodStatus != "Running"'
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 2
            minFailingPeriodsToAlert: 2
          }
        }
      ]
    }
    autoMitigate: true
    actions: {
      actionGroups: empty(alertActionGroupId) ? [] : [
        alertActionGroupId
      ]
    }
  }
}

resource restartingContainersAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: '${name}-container-restarts'
  location: location
  tags: tags
  properties: {
    displayName: '${name}: repeated container restarts'
    description: 'An ARE container restarted repeatedly during the evaluation window.'
    enabled: true
    severity: 2
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    scopes: [
      logAnalyticsWorkspaceId
    ]
    targetResourceTypes: [
      'Microsoft.ContainerService/managedClusters'
    ]
    criteria: {
      allOf: [
        {
          query: 'KubePodInventory | where Namespace == "${kubernetesNamespace}" | summarize Restarts=max(ContainerRestartCount) by PodUid | where Restarts >= 3'
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: true
    actions: {
      actionGroups: empty(alertActionGroupId) ? [] : [
        alertActionGroupId
      ]
    }
  }
}

output clusterName string = aks.name
output backendIdentityClientId string = backendIdentity.properties.clientId
output oboIdentityClientId string = deployObo ? oboIdentity!.properties.clientId : ''
output oidcIssuerUrl string = aks.properties.oidcIssuerProfile.issuerURL
output operationsWorkbookId string = operationsWorkbook.id
