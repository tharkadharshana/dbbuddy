/**
 * DataMind JavaScript SDK — Partner API v1 stub.
 *
 * Works in Node.js (uses fetch via node-fetch or native fetch ≥18) and browsers.
 *
 * Usage:
 *   import DataMind from './datamind.js'
 *   const client = new DataMind({ apiKey: 'pk_...', baseUrl: 'https://api.datamind.ai' })
 *
 *   const integrations = await client.integrations.list('user@example.com')
 *   const sync = await client.sync.trigger('loyverse', { userEmail: 'user@example.com' })
 *   const records = await client.records.list('loyverse', 'products', { userEmail: 'user@example.com' })
 *   const analytics = await client.analytics.run('rfm_analysis', { provider: 'loyverse', userEmail: 'user@example.com' })
 *   const usage = await client.usage.get('user@example.com')
 */

class DataMindResource {
  constructor(client) {
    this._client = client
  }

  _get(path, params = {}) {
    return this._client._request('GET', path, { params })
  }

  _post(path, body) {
    return this._client._request('POST', path, { body })
  }
}

class IntegrationsResource extends DataMindResource {
  list(userEmail) {
    return this._get('/v1/integrations', { user_email: userEmail })
  }
}

class SyncResource extends DataMindResource {
  trigger(provider, { userEmail, full = false } = {}) {
    return this._post(`/v1/sync/${provider}`, { user_email: userEmail, full })
  }
}

class RecordsResource extends DataMindResource {
  list(provider, recordType, { userEmail, limit = 100, offset = 0, storeId, from: fromDate } = {}) {
    const params = { user_email: userEmail, limit, offset }
    if (storeId) params.store_id = storeId
    if (fromDate) params.from = fromDate
    return this._get(`/v1/records/${provider}/${recordType}`, params)
  }
}

class AnalyticsResource extends DataMindResource {
  run(templateId, { provider, userEmail } = {}) {
    return this._get(`/v1/analytics/${templateId}`, { provider, user_email: userEmail })
  }
}

class UsageResource extends DataMindResource {
  get(userEmail) {
    return this._get('/v1/usage', { user_email: userEmail })
  }
}

export default class DataMind {
  constructor({ apiKey, baseUrl = 'https://api.datamind.ai' } = {}) {
    this._apiKey = apiKey
    this._baseUrl = baseUrl.replace(/\/$/, '')
    this.integrations = new IntegrationsResource(this)
    this.sync = new SyncResource(this)
    this.records = new RecordsResource(this)
    this.analytics = new AnalyticsResource(this)
    this.usage = new UsageResource(this)
  }

  async _request(method, path, { params, body } = {}) {
    let url = this._baseUrl + path
    if (params && Object.keys(params).length > 0) {
      url += '?' + new URLSearchParams(params).toString()
    }
    const res = await fetch(url, {
      method,
      headers: {
        'X-API-Key': this._apiKey,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      body: body ? JSON.stringify(body) : undefined,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }))
      throw Object.assign(new Error(err.error || res.statusText), { status: res.status, body: err })
    }
    return res.json()
  }
}
