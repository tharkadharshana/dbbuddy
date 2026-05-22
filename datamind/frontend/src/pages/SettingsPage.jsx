import React, { useState, useEffect } from 'react'
import {
  fetchSettings, patchSettings, addDBConfig, updateDBConfig,
  deleteDBConfig, activateDBConfig, testDBConnection,
  fetchCacheStatus, rebuildCache, deleteAccount,
  getDeveloperKey, generateDeveloperKey, revokeDeveloperKey,
} from '../utils/api'
import { Card, Btn, Badge, Spinner, ErrorBox } from '../components/UI'

function Section({ title, subtitle, children }) {
  return (
    <div style={{ marginBottom: 28 }}>
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)', marginBottom: 3 }}>{title}</div>
        {subtitle && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{subtitle}</div>}
      </div>
      {children}
    </div>
  )
}

function Field({ label, hint, children }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <label style={{ fontSize: 12, color: 'var(--text2)', display: 'block', marginBottom: 5, fontWeight: 500 }}>{label}</label>
      {children}
      {hint && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>{hint}</div>}
    </div>
  )
}

function TextInput({ value, onChange, placeholder, type = 'text', mono = false }) {
  return (
    <input
      type={type} value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      style={{ width: '100%', padding: '9px 12px', fontFamily: mono ? 'var(--mono)' : 'var(--font)', fontSize: 13, borderRadius: 'var(--r-md)' }}
    />
  )
}

const EMPTY_DB = { name: '', host: 'localhost', port: 3306, database: '', user: 'root', password: '' }

// ── Cache Status Card ─────────────────────────────────────────────────────────
function CacheStatusCard({ dbIndex, isActive }) {
  const [status, setStatus]     = useState(null)
  const [rebuilding, setRebuilding] = useState(false)

  useEffect(() => {
    if (!isActive) return
    fetchCacheStatus().then(setStatus).catch(() => {})
  }, [isActive])

  async function handleRebuild() {
    setRebuilding(true)
    try {
      await rebuildCache()
      // Poll until done
      const poll = setInterval(async () => {
        const s = await fetchCacheStatus()
        setStatus(s)
        if (s?.build?.status === 'done' || s?.build?.status === 'error') {
          clearInterval(poll)
          setRebuilding(false)
        }
      }, 2000)
    } catch(e) {
      setRebuilding(false)
    }
  }

  if (!isActive || !status) return null

  const cached   = status.cached
  const building = status?.build?.status === 'building'
  const progress = status?.build?.progress || []

  return (
    <div style={{
      marginTop: 10, padding: '12px 14px', borderRadius: 'var(--r-md)',
      background: cached ? 'var(--green-dim)' : 'var(--amber-dim)',
      border: `1px solid ${cached ? 'rgba(52,209,122,0.2)' : 'rgba(245,166,35,0.2)'}`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: cached || building ? 8 : 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{
            width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
            background: cached ? 'var(--green)' : building ? 'var(--amber)' : 'var(--text3)',
            boxShadow: cached ? '0 0 6px var(--green)' : building ? '0 0 6px var(--amber)' : 'none',
          }} />
          <span style={{ fontSize: 12, fontWeight: 600, color: cached ? 'var(--green)' : 'var(--amber)' }}>
            {cached
              ? `Analytics Cache Ready — ${status.template_count} templates`
              : building
              ? 'Building cache…'
              : 'No cache built yet'}
          </span>
        </div>
        {!building && (
          <button onClick={handleRebuild} disabled={rebuilding} style={{
            padding: '4px 12px', borderRadius: 'var(--r-sm)', fontSize: 11, fontWeight: 500,
            background: 'transparent', border: `1px solid ${cached ? 'rgba(52,209,122,0.3)' : 'rgba(245,166,35,0.3)'}`,
            color: cached ? 'var(--green)' : 'var(--amber)', cursor: rebuilding ? 'not-allowed' : 'pointer'
          }}>
            {rebuilding ? '…' : '↺ Rebuild'}
          </button>
        )}
      </div>

      {cached && status.built_at && (
        <div style={{ fontSize: 11, color: 'var(--text3)' }}>
          Last built: {new Date(status.built_at).toLocaleString()} · Loads instantly on every visit
        </div>
      )}

      {!cached && !building && (
        <div style={{ fontSize: 11, color: 'var(--text3)' }}>
          Activate this DB and visit Analytics Hub to build the cache, or click Rebuild above.
        </div>
      )}

      {building && progress.length > 0 && (
        <div style={{ fontSize: 11, color: 'var(--amber)', fontFamily: 'var(--mono)', marginTop: 6, lineHeight: 1.8 }}>
          {progress.slice(-4).map((l, i) => <div key={i}>{l}</div>)}
        </div>
      )}
    </div>
  )
}

export default function SettingsPage({ user, onLogout, onNavigate, sub }) {
  const [settings, setSettings]   = useState(null)
  const [loading, setLoading]     = useState(true)
  const [saving, setSaving]       = useState(false)
  const [saved, setSaved]         = useState('')
  const [error, setError]         = useState('')

  // API key state (Pro only)
  const [apiKeyInfo, setApiKeyInfo]     = useState(null)   // masked key info from GET
  const [newApiKey, setNewApiKey]       = useState(null)   // full key shown once after generation
  const [apiKeyLoading, setApiKeyLoading] = useState(false)
  const [apiKeyCopied, setApiKeyCopied]   = useState(false)
  const isPro = sub?.plan_name === 'Pro'

  const [geminiKey, setGeminiKey]       = useState('')
  const [deepseekKey, setDeepseekKey]   = useState('')
  const [defaultLLM, setDefaultLLM]     = useState('gemini')

  const [showDBForm, setShowDBForm]   = useState(false)
  const [editingIdx, setEditingIdx]   = useState(null)
  const [dbForm, setDbForm]           = useState(EMPTY_DB)
  const [testing, setTesting]         = useState(false)
  const [testResult, setTestResult]   = useState(null)
  const [dbError, setDbError]         = useState('')

  useEffect(() => {
    fetchSettings()
      .then(s => {
        setSettings(s)
        setGeminiKey(s.gemini_api_key || '')
        setDeepseekKey(s.deepseek_api_key || '')
        setDefaultLLM(s.default_llm || 'gemini')
        setLoading(false)
      })
      .catch(e => { setError(e.response?.data?.detail || e.message); setLoading(false) })
  }, [])

  // Load existing API key info for Pro users
  useEffect(() => {
    if (!isPro) return
    getDeveloperKey().then(r => setApiKeyInfo(r.key)).catch(() => {})
  }, [isPro])

  async function handleGenerateKey() {
    if (!window.confirm('Generate a new API key? Your existing key will be revoked immediately.')) return
    setApiKeyLoading(true)
    try {
      const r = await generateDeveloperKey()
      setNewApiKey(r.key)
      setApiKeyInfo(null) // will refresh on next load
    } catch (e) {
      alert(e.response?.data?.detail || 'Failed to generate key')
    } finally { setApiKeyLoading(false) }
  }

  async function handleRevokeKey() {
    if (!window.confirm('Revoke your API key? Any scripts using it will stop working immediately.')) return
    setApiKeyLoading(true)
    try {
      await revokeDeveloperKey()
      setApiKeyInfo(null)
      setNewApiKey(null)
    } catch (e) {
      alert(e.response?.data?.detail || 'Failed to revoke key')
    } finally { setApiKeyLoading(false) }
  }

  function handleCopyKey(key) {
    navigator.clipboard.writeText(key).then(() => {
      setApiKeyCopied(true)
      setTimeout(() => setApiKeyCopied(false), 2000)
    })
  }

  async function saveAPIKeys() {
    setSaving(true); setSaved(''); setError('')
    try {
      await patchSettings({ gemini_api_key: geminiKey, deepseek_api_key: deepseekKey, default_llm: defaultLLM })
      setSaved('Saved!')
      setTimeout(() => setSaved(''), 2500)
    } catch(e) { setError(e.response?.data?.detail || e.message) }
    finally { setSaving(false) }
  }

  async function handleTestDB() {
    setTesting(true); setTestResult(null); setDbError('')
    try {
      const r = await testDBConnection(dbForm)
      setTestResult(r)
    } catch(e) { setDbError(e.response?.data?.detail || e.message) }
    finally { setTesting(false) }
  }

  async function handleSaveDB() {
    if (!dbForm.name || !dbForm.host || !dbForm.database) {
      setDbError('Name, host and database are required'); return
    }
    setDbError(''); setSaving(true)
    try {
      if (editingIdx !== null) {
        await updateDBConfig(editingIdx, dbForm)
      } else {
        await addDBConfig(dbForm)
      }
      const s = await fetchSettings()
      setSettings(s)
      setShowDBForm(false); setEditingIdx(null); setDbForm(EMPTY_DB); setTestResult(null)
    } catch(e) { setDbError(e.response?.data?.detail || e.message) }
    finally { setSaving(false) }
  }

  async function handleDeleteDB(i) {
    if (!window.confirm('Delete this database connection?')) return
    await deleteDBConfig(i)
    const s = await fetchSettings(); setSettings(s)
  }

  async function handleActivateDB(i) {
    await activateDBConfig(i)
    const s = await fetchSettings(); setSettings(s)
  }

  function openEditDB(i) {
    const cfg = settings.db_configs[i]
    setDbForm({ ...cfg, password: '' })
    setEditingIdx(i); setShowDBForm(true); setTestResult(null); setDbError('')
  }

  if (loading) return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: 32, color: 'var(--text3)' }}>
      <Spinner /> Loading settings…
    </div>
  )

  const s = settings || {}
  const configs = s.db_configs || []

  const inp = (value, onChange, placeholder, type = 'text', mono = false) => (
    <input type={type} value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
      style={{ width: '100%', padding: '9px 12px', fontFamily: mono ? 'var(--mono)' : 'var(--font)', fontSize: 13, borderRadius: 'var(--r-md)' }} />
  )

  return (
    <div style={{ maxWidth: 700, padding: '24px 24px 60px' }}>
      <div style={{ marginBottom: 28 }}>
        <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>Settings</div>
        <div style={{ fontSize: 13, color: 'var(--text3)' }}>Manage your API keys, database connections, and account.</div>
      </div>

      {error && <div style={{ marginBottom: 16 }}><ErrorBox message={error} /></div>}

      {/* ── Account ─────────────────────────────────────────────────────── */}
      <Section title="Account">
        <Card style={{ padding: '16px 18px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <div style={{ width: 42, height: 42, borderRadius: '50%', background: 'linear-gradient(135deg,#4f8ef7,#a78bfa)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: 17, color: '#fff', flexShrink: 0 }}>
              {user?.name?.[0]?.toUpperCase() || '?'}
            </div>
            <div>
              <div style={{ fontWeight: 600, fontSize: 14 }}>{user?.name}</div>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>{user?.email}</div>
            </div>
          </div>
          <div style={{ display:'flex', gap:8 }}>
            <Btn variant="danger" size="sm" onClick={onLogout}>Sign Out</Btn>
            <Btn variant="danger" size="sm" onClick={async () => {
              if (!window.confirm('Permanently delete your account and all data? This cannot be undone.')) return
              if (!window.confirm('Are you sure? All integrations, synced data and settings will be destroyed.')) return
              try {
                await deleteAccount()
                localStorage.removeItem('dm_token')
                localStorage.removeItem('dm_user')
                window.location.href = '/'
              } catch(e) {
                alert('Delete failed: ' + (e.response?.data?.detail || e.message))
              }
            }}>Delete Account</Btn>
          </div>
        </Card>
      </Section>

      {/* ── LLM API Keys — hidden, re-enable when switching to BYOK model ──
      <Section title="LLM API Keys" subtitle="Your keys are stored per-account and never shared. Used for NL queries, report generation, and the one-time cache build.">
        <Card style={{ padding: '18px 20px' }}>
          <Field label="Default LLM">
            <div style={{ display: 'flex', gap: 8 }}>
              {['gemini', 'deepseek'].map(llm => (
                <button key={llm} onClick={() => setDefaultLLM(llm)} style={{
                  flex: 1, padding: '9px 0', borderRadius: 'var(--r-md)', fontSize: 13, fontWeight: 500,
                  background: defaultLLM === llm ? 'var(--blue-dim)' : 'var(--bg3)',
                  color: defaultLLM === llm ? 'var(--blue)' : 'var(--text3)',
                  border: `1px solid ${defaultLLM === llm ? 'rgba(79,142,247,0.3)' : 'var(--border)'}`,
                  cursor: 'pointer'
                }}>
                  {llm === 'gemini' ? '✦ Gemini' : '◈ DeepSeek'}
                </button>
              ))}
            </div>
          </Field>
          <Field label="Gemini API Key" hint="Get yours free at aistudio.google.com → API Keys">
            <div style={{ display: 'flex', gap: 8 }}>
              {inp(geminiKey, setGeminiKey, geminiKey ? '••••••••••••••••' : 'AIza…', 'password', true)}
              <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noreferrer"
                style={{ padding: '9px 14px', background: 'var(--bg3)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', fontSize: 12, color: 'var(--text3)', textDecoration: 'none', display: 'flex', alignItems: 'center', whiteSpace: 'nowrap' }}>
                Get key ↗
              </a>
            </div>
          </Field>
          <Field label="DeepSeek API Key" hint="Get yours at platform.deepseek.com → API Keys">
            <div style={{ display: 'flex', gap: 8 }}>
              {inp(deepseekKey, setDeepseekKey, deepseekKey ? '••••••••••••••••' : 'sk-…', 'password', true)}
              <a href="https://platform.deepseek.com/api_keys" target="_blank" rel="noreferrer"
                style={{ padding: '9px 14px', background: 'var(--bg3)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', fontSize: 12, color: 'var(--text3)', textDecoration: 'none', display: 'flex', alignItems: 'center', whiteSpace: 'nowrap' }}>
                Get key ↗
              </a>
            </div>
          </Field>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Btn onClick={saveAPIKeys} disabled={saving}>
              {saving ? <><Spinner size={12} color="#fff" /> Saving…</> : 'Save API Keys'}
            </Btn>
            {saved && <span style={{ fontSize: 12, color: 'var(--green)' }}>✓ {saved}</span>}
          </div>
        </Card>
      </Section>
      ── end LLM API Keys ── */}

      {/* ── Database Connections ──────────────────────────────────────────── */}
      <Section title="Database Connections"
        subtitle="Add your MySQL databases. When you save or activate a connection, the AI automatically builds a custom SQL cache for your schema — one time only.">

        {configs.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 14 }}>
            {configs.map((cfg, i) => {
              const isActive = s.active_db_index === i
              return (
                <Card key={i} style={{ padding: '14px 16px', borderColor: isActive ? 'rgba(79,142,247,0.4)' : 'var(--border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <div style={{
                      width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                      background: isActive ? 'var(--green)' : 'var(--text3)',
                      boxShadow: isActive ? '0 0 8px var(--green)' : 'none'
                    }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: 13 }}>{cfg.name}</div>
                      <div style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--mono)' }}>
                        {cfg.user}@{cfg.host}:{cfg.port}/{cfg.database}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                      {isActive
                        ? <Badge color="green">Active</Badge>
                        : <Btn size="sm" variant="success" onClick={() => handleActivateDB(i)}>Use</Btn>}
                      <Btn size="sm" variant="ghost" onClick={() => openEditDB(i)}>Edit</Btn>
                      <Btn size="sm" variant="danger" onClick={() => handleDeleteDB(i)}>✕</Btn>
                    </div>
                  </div>

                  {/* Cache status row for this DB */}
                  <CacheStatusCard dbIndex={i} isActive={isActive} />
                </Card>
              )
            })}
          </div>
        )}

        {!showDBForm && (
          <Btn variant="ghost" onClick={() => { setShowDBForm(true); setEditingIdx(null); setDbForm(EMPTY_DB); setTestResult(null); setDbError('') }}>
            + Add Database Connection
          </Btn>
        )}

        {showDBForm && (
          <Card style={{ padding: 20, marginTop: 10 }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 16 }}>
              {editingIdx !== null ? 'Edit Connection' : 'New Database Connection'}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 4 }}>
              <div style={{ gridColumn: '1/-1' }}>
                <Field label="Connection Name">
                  <TextInput value={dbForm.name} onChange={v => setDbForm(f => ({ ...f, name: v }))} placeholder="e.g. Production DB" />
                </Field>
              </div>
              <Field label="Host">
                <TextInput value={dbForm.host} onChange={v => setDbForm(f => ({ ...f, host: v }))} placeholder="localhost" mono />
              </Field>
              <Field label="Port">
                <TextInput value={dbForm.port} onChange={v => setDbForm(f => ({ ...f, port: Number(v) }))} placeholder="3306" mono />
              </Field>
              <Field label="Database Name">
                <TextInput value={dbForm.database} onChange={v => setDbForm(f => ({ ...f, database: v }))} placeholder="my_database" mono />
              </Field>
              <Field label="Username">
                <TextInput value={dbForm.user} onChange={v => setDbForm(f => ({ ...f, user: v }))} placeholder="root" mono />
              </Field>
              <div style={{ gridColumn: '1/-1' }}>
                <Field label="Password" hint={editingIdx !== null ? 'Leave blank to keep the existing password.' : 'Stored securely per-account.'}>
                  <TextInput type="password" value={dbForm.password} onChange={v => setDbForm(f => ({ ...f, password: v }))} placeholder="••••••••" />
                </Field>
              </div>
            </div>

            {dbError && <div style={{ marginBottom: 12 }}><ErrorBox message={dbError} /></div>}

            {testResult && (
              <div style={{
                marginBottom: 12, padding: '12px 14px', borderRadius: 'var(--r-md)', fontSize: 12,
                background: testResult.ok ? 'var(--green-dim)' : 'var(--red-dim)',
                border: `1px solid ${testResult.ok ? 'rgba(52,209,122,0.2)' : 'rgba(240,80,80,0.2)'}`,
                color: testResult.ok ? 'var(--green)' : 'var(--red)'
              }}>
                {testResult.ok
                  ? `✓ Connected! Found ${testResult.table_count} tables: ${testResult.tables?.slice(0, 6).join(', ')}${testResult.table_count > 6 ? '…' : ''}`
                  : `✗ Failed: ${testResult.error}`}
              </div>
            )}

            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <Btn onClick={handleTestDB} disabled={testing} variant="ghost">
                {testing ? <><Spinner size={12} /> Testing…</> : '⚡ Test Connection'}
              </Btn>
              <Btn onClick={handleSaveDB} disabled={saving}>
                {saving ? <><Spinner size={12} color="#fff" /> Saving…</> : 'Save Connection'}
              </Btn>
              <Btn variant="ghost" onClick={() => { setShowDBForm(false); setEditingIdx(null); setTestResult(null); setDbError('') }}>
                Cancel
              </Btn>
            </div>

            {/* Info box */}
            <div style={{ marginTop: 14, padding: '10px 14px', borderRadius: 'var(--r-md)', background: 'var(--blue-dim)', border: '1px solid rgba(79,142,247,0.15)', fontSize: 12, color: 'var(--blue)' }}>
              🧠 After saving, the AI will automatically read your schema and generate custom SQL queries for all analytics templates. This runs <strong>once</strong> and is cached — future visits load instantly with zero LLM tokens.
            </div>
          </Card>
        )}
      </Section>

      {/* ── How caching works ─────────────────────────────────────────────── */}
      {/* ── How Billing Works ────────────────────────────────────────────── */}
      <Section title="How Billing Works" subtitle="Every operation — AI queries, analytics templates, forecasting, data syncs — consumes Tokens from your plan.">
        <Card style={{ padding: '18px 20px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10, marginBottom: 16 }}>
            {[
              { icon: '🗄', label: 'Data volume',    desc: 'More rows = more Tokens' },
              { icon: '⚙️', label: 'Operation type', desc: 'AI & ML ops cost more than SQL templates' },
              { icon: '📅', label: 'Data history',   desc: 'Plan determines how far back you can query' },
            ].map(({ icon, label, desc }) => (
              <div key={label} style={{ background: 'var(--bg3)', border: '1px solid var(--border)', borderRadius: 8, padding: '12px 14px' }}>
                <div style={{ fontSize: 18, marginBottom: 6 }}>{icon}</div>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)', marginBottom: 3 }}>{label}</div>
                <div style={{ fontSize: 11, color: 'var(--text3)' }}>{desc}</div>
              </div>
            ))}
          </div>

          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 14 }}>
            Every operation deducts Tokens based on what it does and how much of your data it touches.
            Check the <strong style={{ color: 'var(--text2)' }}>Billing → Usage</strong> tab to see a full breakdown of your activity.
          </div>

          <button
            onClick={() => onNavigate && onNavigate('docs')}
            style={{
              padding: '8px 18px', borderRadius: 7, border: '1px solid var(--blue)',
              background: 'var(--blue-dim)', color: 'var(--blue)',
              fontWeight: 600, fontSize: 12, cursor: 'pointer',
            }}
          >
            View full billing docs →
          </button>
        </Card>
      </Section>

      {/* ── API Access (Pro only) ──────────────────────────────────────── */}
      {isPro && (
        <Section
          title="API Access"
          subtitle="Use your personal API key to access DataMind programmatically from scripts, apps, or custom dashboards."
        >
          <Card style={{ padding: '18px 20px' }}>

            {/* New key banner — shown once after generation */}
            {newApiKey && (
              <div style={{
                marginBottom: 16, padding: '14px 16px', borderRadius: 8,
                background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.25)',
              }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--green)', marginBottom: 8 }}>
                  ✓ New API key generated — copy it now. It won't be shown again.
                </div>
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  background: 'var(--bg2)', border: '1px solid var(--border)',
                  borderRadius: 7, padding: '10px 14px',
                }}>
                  <code style={{ flex: 1, fontSize: 12, fontFamily: 'var(--mono)', color: 'var(--text)', wordBreak: 'break-all' }}>
                    {newApiKey}
                  </code>
                  <button
                    onClick={() => handleCopyKey(newApiKey)}
                    style={{
                      flexShrink: 0, padding: '5px 12px', borderRadius: 6, fontSize: 12,
                      background: apiKeyCopied ? 'var(--green)' : 'var(--blue)',
                      color: '#fff', border: 'none', cursor: 'pointer', fontWeight: 600,
                    }}
                  >
                    {apiKeyCopied ? 'Copied!' : 'Copy'}
                  </button>
                </div>
              </div>
            )}

            {/* Existing key info */}
            {!newApiKey && apiKeyInfo && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 6 }}>Active API key</div>
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  background: 'var(--bg2)', border: '1px solid var(--border)',
                  borderRadius: 7, padding: '10px 14px',
                }}>
                  <code style={{ flex: 1, fontSize: 12, fontFamily: 'var(--mono)', color: 'var(--text2)' }}>
                    {apiKeyInfo.masked}
                  </code>
                  <span style={{ fontSize: 11, color: 'var(--text3)', flexShrink: 0 }}>
                    {apiKeyInfo.last_used_at
                      ? `Last used ${new Date(apiKeyInfo.last_used_at).toLocaleDateString()}`
                      : `Created ${new Date(apiKeyInfo.created_at).toLocaleDateString()}`}
                  </span>
                </div>
              </div>
            )}

            {!newApiKey && !apiKeyInfo && (
              <div style={{ marginBottom: 16, fontSize: 13, color: 'var(--text3)' }}>
                No active API key. Generate one below to start using the DataMind API.
              </div>
            )}

            {/* Actions */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 20 }}>
              <Btn onClick={handleGenerateKey} disabled={apiKeyLoading}>
                {apiKeyLoading ? <><Spinner size={12} color="#fff" /> Working…</> : apiKeyInfo || newApiKey ? 'Regenerate Key' : 'Generate API Key'}
              </Btn>
              {(apiKeyInfo || newApiKey) && (
                <Btn variant="ghost" onClick={handleRevokeKey} disabled={apiKeyLoading}>
                  Revoke Key
                </Btn>
              )}
            </div>

            {/* How to use */}
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12 }}>
              Use your key in the <code style={{ background: 'var(--bg3)', padding: '1px 6px', borderRadius: 4 }}>Authorization</code> header:
            </div>
            <div style={{
              background: 'var(--bg2)', border: '1px solid var(--border)',
              borderRadius: 7, padding: '12px 14px', marginBottom: 16,
              fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text2)',
              whiteSpace: 'pre',
            }}>
{`curl https://your-server/api/query \\
  -H "Authorization: Bearer dm_live_your_key_here" \\
  -H "Content-Type: application/json" \\
  -d '{"question": "What was my total revenue last month?", "llm": "deepseek"}'`}
            </div>

            {/* Docs link */}
            <div style={{
              display: 'flex', alignItems: 'center', gap: 10,
              padding: '12px 14px', borderRadius: 8,
              background: 'var(--blue-dim)', border: '1px solid rgba(79,142,247,0.2)',
            }}>
              <span style={{ fontSize: 18 }}>📄</span>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 2 }}>
                  Full API Reference
                </div>
                <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                  Interactive Swagger docs — try every endpoint live in the browser.
                </div>
              </div>
              <a
                href="/docs"
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  flexShrink: 0, padding: '6px 14px', borderRadius: 6,
                  background: 'var(--blue)', color: '#fff',
                  fontSize: 12, fontWeight: 600, textDecoration: 'none',
                }}
              >
                Open Docs →
              </a>
            </div>
          </Card>
        </Section>
      )}

      <Section title="How the Cache Works">
        <Card style={{ padding: '16px 18px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {[
              { step: '1', label: 'You add a database connection', desc: 'We read your schema, table names, column types, foreign keys, and sample rows.' },
              { step: '2', label: 'AI generates custom SQL (one time)', desc: 'The LLM writes optimised SQL for every analytics template — tailored to your exact table and column names.' },
              { step: '3', label: 'SQL is saved to disk', desc: 'Stored in backend/data/cache/. Every user+DB pair has its own cache file.' },
              { step: '4', label: 'All future requests use the cache', desc: 'Zero LLM tokens. Analytics load by running pre-generated SQL directly on your database.' },
              { step: '5', label: 'Cache rebuilds only when you change the DB', desc: 'Edit or swap your DB connection to trigger a fresh build.' },
            ].map(({ step, label, desc }) => (
              <div key={step} style={{ display: 'flex', gap: 14 }}>
                <div style={{ width: 24, height: 24, borderRadius: '50%', background: 'var(--blue-dim)', border: '1px solid rgba(79,142,247,0.3)', color: 'var(--blue)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 700, flexShrink: 0 }}>{step}</div>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text)', marginBottom: 2 }}>{label}</div>
                  <div style={{ fontSize: 12, color: 'var(--text3)' }}>{desc}</div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </Section>
    </div>
  )
}
