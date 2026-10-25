// ForecastPilot on Azure Container Apps: the environment as it runs in
// production (resource group forecastpilot-rg, Germany West Central).
//
// scope decides what a deployment touches:
//   app    - only the Container App; registry and environment are referenced
//            as existing resources. This is what every push to main deploys.
//   shared - Log Analytics, the container registry and the Container Apps
//            environment. Manual only (rebuild, or a change to those).
//   all    - both, in that order. Manual only.
// See README, "Infrastructure", for the rebuild order and the key rotation.

targetScope = 'resourceGroup'

@description('What this deployment touches: app (every push), shared or all (manual).')
@allowed([
  'app'
  'shared'
  'all'
])
param scope string = 'app'

@description('Region of all resources.')
param location string = resourceGroup().location

@description('Azure Container Registry name (admin user enabled; CI pushes with it).')
param registryName string

@description('Log Analytics workspace that receives the container console and system logs.')
param workspaceName string

@description('Container Apps environment name.')
param environmentName string

@description('Container App name.')
param appName string

@description('Repository of the image in the registry.')
param imageRepository string = 'forecastpilot'

@description('Image tag to run: the commit SHA the deploy job pushed.')
@minLength(1)
param imageTag string

@description('Anthropic API key, from the GitHub secret ANTHROPIC_API_KEY. Never empty.')
@secure()
@minLength(1)
param anthropicApiKey string

@description('Claude model for the commentary.')
param anthropicModel string

@description('USD per million input tokens, for cost_usd.')
param anthropicPriceInputPerMtok string

@description('USD per million output tokens, for cost_usd.')
param anthropicPriceOutputPerMtok string

@description('Networks of trusted reverse proxies (the Container Apps ingress).')
param trustedProxyCidrs string

@description('Browser origins allowed to call the API (the GitHub Pages origin).')
param corsOrigins string

@description('vCPU per replica, as a decimal string (Bicep has no float type).')
param cpu string = '0.5'

@description('Memory per replica.')
param memory string = '1Gi'

@description('Upper bound of the scale rule. The lower bound is the platform default 0 (scale to zero).')
@minValue(1)
param maxReplicas int

module shared 'modules/shared.bicep' = if (scope != 'app') {
  name: 'shared'
  params: {
    location: location
    registryName: registryName
    workspaceName: workspaceName
    environmentName: environmentName
  }
}

module app 'modules/app.bicep' = if (scope != 'shared') {
  name: 'app'
  params: {
    location: location
    appName: appName
    registryName: registryName
    environmentName: environmentName
    image: '${registryName}${environment().suffixes.acrLoginServer}/${imageRepository}:${imageTag}'
    anthropicApiKey: anthropicApiKey
    anthropicModel: anthropicModel
    anthropicPriceInputPerMtok: anthropicPriceInputPerMtok
    anthropicPriceOutputPerMtok: anthropicPriceOutputPerMtok
    trustedProxyCidrs: trustedProxyCidrs
    corsOrigins: corsOrigins
    cpu: cpu
    memory: memory
    maxReplicas: maxReplicas
  }
  // Only effective with scope=all; ARM drops the dependency when shared is skipped.
  dependsOn: [
    shared
  ]
}
