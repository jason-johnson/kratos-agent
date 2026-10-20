@description('Name of the VNet')
param name string

@description('Location for the VNet')
param location string

@description('Tags for resources')
param tags object = {}

resource vnet 'Microsoft.Network/virtualNetworks@2023-09-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: ['10.0.0.0/16']
    }
    subnets: [
      {
        name: 'private-endpoints'
        properties: {
          addressPrefix: '10.0.2.0/24'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: 'aks'
        properties: {
          addressPrefix: '10.0.4.0/22'
        }
      }
    ]
  }
}

output id string = vnet.id
output name string = vnet.name
output privateEndpointSubnetId string = vnet.properties.subnets[0].id
output aksSubnetId string = vnet.properties.subnets[1].id
