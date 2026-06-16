import assert from 'node:assert/strict'

import {
  authConfigToBuilderForm,
  buildAuthConfigFromBuilderForm,
  createDefaultAuthBuilderForm,
  extractSecretFieldNames
} from '../mcpAuthConfigBuilder.js'

const run = () => {
  {
    const form = createDefaultAuthBuilderForm()
    assert.equal(buildAuthConfigFromBuilderForm(form), null)
  }

  {
    const form = createDefaultAuthBuilderForm('custom_http_token')
    form.bindingScope = 'department'
    form.injectEntries = [
      { name: 'Authorization', value_template: 'Bearer ${access_token}' },
      { name: 'X-Yuxi-User', value_template: '${context.user_id}' }
    ]
    form.tokenUrl = 'http://internal-gateway/token'
    form.tokenHeaders = [{ key: 'Content-Type', value: 'application/json' }]
    form.tokenBodyTemplate = [
      { key: 'client_id', value: '${secret.client_id}' },
      { key: 'client_secret', value: '${secret.client_secret}' },
      { key: 'user_id', value: '${context.user_id}' }
    ]
    form.tokenResponseMap = [
      { key: 'access_token', value: 'data.access_token' },
      { key: 'expires_in', value: 'data.expires_in' }
    ]

    const config = buildAuthConfigFromBuilderForm(form)
    assert.equal(config.provider, 'custom_http_token')
    assert.equal(config.binding_scope, 'department')
    assert.equal(config.manifest_scope, 'binding')
    assert.deepEqual(config.inject.entries, form.injectEntries)
    assert.deepEqual(config.token_request, {
      url: 'http://internal-gateway/token',
      method: 'POST',
      body_type: 'json',
      headers: { 'Content-Type': 'application/json' },
      body_template: {
        client_id: '${secret.client_id}',
        client_secret: '${secret.client_secret}',
        user_id: '${context.user_id}'
      },
      response_map: {
        access_token: 'data.access_token',
        expires_in: 'data.expires_in'
      }
    })
    assert.deepEqual(extractSecretFieldNames(config), ['client_id', 'client_secret'])
  }

  {
    const config = {
      version: 1,
      provider: 'bound_secret',
      binding_scope: 'user',
      manifest_scope: 'binding',
      inject: {
        target: 'headers',
        entries: [{ name: 'X-Api-Key', value_template: '${secret.api_key}' }]
      },
      refresh_policy: {
        pre_refresh_seconds: 120,
        retry_once_on_401: false
      }
    }

    const form = authConfigToBuilderForm(config)
    assert.equal(form.provider, 'bound_secret')
    assert.equal(form.bindingScope, 'user')
    assert.deepEqual(form.injectEntries, [{ name: 'X-Api-Key', value_template: '${secret.api_key}' }])
    assert.deepEqual(buildAuthConfigFromBuilderForm(form), config)
  }

  console.log('mcpAuthConfigBuilder: all assertions passed')
}

run()
