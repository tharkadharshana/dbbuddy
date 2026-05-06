import React, { useState, useEffect } from 'react'
import { fetchSettings, patchSettings, addDBConfig, updateDBConfig, deleteDBConfig, activateDBConfig, testDBConnection } from '../utils/api'
import { Card, Btn, Badge, Spinner, ErrorBox } from '../components/UI'

// ── Section wrapper ──────────────────────────────────────────────────────────
function Section({ title, subtitle, children }) {
  return (
    <div style={{ marginBottom:28 }}>
      <div style={{ marginBottom:14 }}>
        <div style={{ fontSize:15, fontWeight:600, color:'var(--text)', marginBottom:3 }}>{title}</div>
        {subtitle && <div style={{ fontSize:12, color:'var(--text3)' }}>{subtitle}</div>}
      </div>
      {children}
    </div>
  )
}

// ── Labelled input ───────────────────────────────────────────────────────────
function Field({ label, hint, children }) {
  return (
    <div style={{ marginBottom:14 }}>
      <label style={{ fontSize:12, color:'var(--text2)', display:'block', marginBottom:5, fontWeight:500 }}>{label}</label>
      {children}
      {hint && <div style={{ fontSize:11, color:'var(--text3)', marginTop:4 }}>{hint}</div>}
    </div>
  )
}

function TextInput({ value, onChange, placeholder, type='text', mono=false }) {
  return (
    <input type={type} value={value} onChange={e=>onChange(e.target.value)} placeholder={placeholder} style={{ width:'100%', padding:'9px 12px', fontFamily: mono ? 'var(--mono)' : 'var(--font)', fontSize:13, borderRadius:'var(--r-md)' }} />
  )
}

// ── Empty DB config form ─────────────────────────────────────────────────────
const EMPTY_DB = { name:'', host:'localhost', port:3306, database:'', user:'root', password:'' }

export default function SettingsPage({ user, onLogout }) {
  const [settings, setSettings] = useState(null)
  const [loading, setLoading]   = useState(true)
  const [saving, setSaving]     = useState(false)
  const [saved, setSaved]       = useState('')
  const [error, setError]       = useState('')

  // API key fields
  const [geminiKey, setGeminiKey]     = useState('')
  const [deepseekKey, setDeepseekKey] = useState('')
  const [defaultLLM, setDefaultLLM]   = useState('gemini')

  // DB form
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

  async function saveAPIKeys() {
    setSaving(true); setSaved(''); setError('')
    try {
      await patchSettings({ gemini_api_key: geminiKey, deepseek_api_key: deepseekKey, default_llm: defaultLLM })
      setSaved('API keys saved!')
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
    setDbForm({ ...cfg, password:'' })
    setEditingIdx(i); setShowDBForm(true); setTestResult(null); setDbError('')
  }

  if (loading) return <div style={{ padding:32 }}><Spinner /> Loading settings…</div>

  const s = settings || {}
  const configs = s.db_configs || []

  return (
    <div style={{ maxWidth:700, padding:'24px 24px 48px' }}>
      <div style={{ marginBottom:28 }}>
        <div style={{ fontSize:20, fontWeight:700, marginBottom:4 }}>Settings</div>
        <div style={{ fontSize:13, color:'var(--text3)' }}>Manage your API keys, database connections, and account preferences.</div>
      </div>

      {error && <div style={{ marginBottom:16 }}><ErrorBox message={error} /></div>}

      {/* ── Account ────────────────────────────────────────────────────── */}
      <Section title="Account" subtitle="Your profile information">
        <Card style={{ padding:'16px 18px', display:'flex', alignItems:'center', justifyContent:'space-between' }}>
          <div style={{ display:'flex', alignItems:'center', gap:14 }}>
            <div style={{ width:40, height:40, borderRadius:'50%', background:'linear-gradient(135deg,#4f8ef7,#a78bfa)', display:'flex', alignItems:'center', justifyContent:'center', fontWeight:700, fontSize:16, color:'#fff', flexShrink:0 }}>
              {user?.name?.[0]?.toUpperCase() || '?'}
            </div>
            <div>
              <div style={{ fontWeight:600, fontSize:14 }}>{user?.name}</div>
              <div style={{ fontSize:12, color:'var(--text3)' }}>{user?.email}</div>
            </div>
          </div>
          <Btn variant="danger" size="sm" onClick={onLogout}>Sign Out</Btn>
        </Card>
      </Section>

      {/* ── LLM API Keys ──────────────────────────────────────────────── */}
      <Section title="LLM API Keys" subtitle="Your keys are stored securely and used only for your queries. They are never shared.">
        <Card style={{ padding:'18px 20px' }}>
          <Field label="Default LLM">
            <div style={{ display:'flex', gap:8 }}>
              {['gemini','deepseek'].map(llm => (
                <button key={llm} onClick={() => setDefaultLLM(llm)} style={{
                  flex:1, padding:'9px 0', borderRadius:'var(--r-md)', fontSize:13, fontWeight:500,
                  background: defaultLLM===llm ? 'var(--blue-dim)' : 'var(--bg3)',
                  color: defaultLLM===llm ? 'var(--blue)' : 'var(--text3)',
                  border: `1px solid ${defaultLLM===llm ? 'rgba(79,142,247,0.3)' : 'var(--border)'}`,
                  cursor:'pointer'
                }}>
                  {llm==='gemini' ? '✦ Gemini' : '◈ DeepSeek'}
                </button>
              ))}
            </div>
          </Field>

          <Field label="Gemini API Key" hint="Get yours at aistudio.google.com → API Keys">
            <div style={{ display:'flex', gap:8 }}>
              <input type="password" value={geminiKey} onChange={e=>setGeminiKey(e.target.value)} placeholder={geminiKey ? '••••••••••••••••' : 'AIza…'} style={{ flex:1, padding:'9px 12px', fontFamily:'var(--mono)', fontSize:13, borderRadius:'var(--r-md)' }} />
              <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noreferrer" style={{ padding:'9px 14px', background:'var(--bg3)', border:'1px solid var(--border)', borderRadius:'var(--r-md)', fontSize:12, color:'var(--text3)', textDecoration:'none', display:'flex', alignItems:'center', whiteSpace:'nowrap' }}>Get key ↗</a>
            </div>
          </Field>

          <Field label="DeepSeek API Key" hint="Get yours at platform.deepseek.com → API Keys">
            <div style={{ display:'flex', gap:8 }}>
              <input type="password" value={deepseekKey} onChange={e=>setDeepseekKey(e.target.value)} placeholder={deepseekKey ? '••••••••••••••••' : 'sk-…'} style={{ flex:1, padding:'9px 12px', fontFamily:'var(--mono)', fontSize:13, borderRadius:'var(--r-md)' }} />
              <a href="https://platform.deepseek.com/api_keys" target="_blank" rel="noreferrer" style={{ padding:'9px 14px', background:'var(--bg3)', border:'1px solid var(--border)', borderRadius:'var(--r-md)', fontSize:12, color:'var(--text3)', textDecoration:'none', display:'flex', alignItems:'center', whiteSpace:'nowrap' }}>Get key ↗</a>
            </div>
          </Field>

          <div style={{ display:'flex', alignItems:'center', gap:12 }}>
            <Btn onClick={saveAPIKeys} disabled={saving}>
              {saving ? <><Spinner size={12} color="#fff" /> Saving…</> : 'Save API Keys'}
            </Btn>
            {saved && <span style={{ fontSize:12, color:'var(--green)' }}>✓ {saved}</span>}
          </div>
        </Card>
      </Section>

      {/* ── Database Connections ─────────────────────────────────────────── */}
      <Section title="Database Connections" subtitle="Add your MySQL databases. The active connection is used for all analytics.">
        {configs.length > 0 && (
          <div style={{ display:'flex', flexDirection:'column', gap:8, marginBottom:12 }}>
            {configs.map((cfg, i) => (
              <Card key={i} style={{ padding:'14px 16px', borderColor: s.active_db_index===i ? 'rgba(79,142,247,0.4)' : 'var(--border)' }}>
                <div style={{ display:'flex', alignItems:'center', gap:12 }}>
                  <div style={{ width:8, height:8, borderRadius:'50%', background: s.active_db_index===i ? 'var(--green)' : 'var(--text3)', boxShadow: s.active_db_index===i ? '0 0 8px var(--green)' : 'none', flexShrink:0 }} />
                  <div style={{ flex:1, minWidth:0 }}>
                    <div style={{ fontWeight:600, fontSize:13 }}>{cfg.name}</div>
                    <div style={{ fontSize:11, color:'var(--text3)', fontFamily:'var(--mono)' }}>
                      {cfg.user}@{cfg.host}:{cfg.port}/{cfg.database}
                    </div>
                  </div>
                  <div style={{ display:'flex', gap:6 }}>
                    {s.active_db_index !== i && (
                      <Btn size="sm" variant="success" onClick={() => handleActivateDB(i)}>Use</Btn>
                    )}
                    {s.active_db_index === i && <Badge color="green">Active</Badge>}
                    <Btn size="sm" variant="ghost" onClick={() => openEditDB(i)}>Edit</Btn>
                    <Btn size="sm" variant="danger" onClick={() => handleDeleteDB(i)}>Delete</Btn>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}

        {!showDBForm && (
          <Btn variant="ghost" onClick={() => { setShowDBForm(true); setEditingIdx(null); setDbForm(EMPTY_DB); setTestResult(null); setDbError('') }}>
            + Add Database Connection
          </Btn>
        )}

        {showDBForm && (
          <Card style={{ padding:'20px', marginTop:8 }}>
            <div style={{ fontSize:14, fontWeight:600, marginBottom:16, color:'var(--text)' }}>
              {editingIdx !== null ? 'Edit Connection' : 'New Database Connection'}
            </div>

            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12, marginBottom:12 }}>
              <div style={{ gridColumn:'1/-1' }}>
                <Field label="Connection Name">
                  <TextInput value={dbForm.name} onChange={v=>setDbForm(f=>({...f,name:v}))} placeholder="e.g. Production DB, Local Dev" />
                </Field>
              </div>
              <Field label="Host">
                <TextInput value={dbForm.host} onChange={v=>setDbForm(f=>({...f,host:v}))} placeholder="localhost or IP" mono />
              </Field>
              <Field label="Port">
                <TextInput value={dbForm.port} onChange={v=>setDbForm(f=>({...f,port:Number(v)}))} placeholder="3306" mono />
              </Field>
              <Field label="Database Name">
                <TextInput value={dbForm.database} onChange={v=>setDbForm(f=>({...f,database:v}))} placeholder="my_database" mono />
              </Field>
              <Field label="Username">
                <TextInput value={dbForm.user} onChange={v=>setDbForm(f=>({...f,user:v}))} placeholder="root" mono />
              </Field>
              <div style={{ gridColumn:'1/-1' }}>
                <Field label="Password" hint="Password is encrypted and never returned after saving.">
                  <TextInput type="password" value={dbForm.password} onChange={v=>setDbForm(f=>({...f,password:v}))} placeholder={editingIdx !== null ? 'Leave blank to keep existing password' : '••••••••'} />
                </Field>
              </div>
            </div>

            {dbError && <div style={{ marginBottom:12 }}><ErrorBox message={dbError} /></div>}

            {testResult && (
              <div style={{ marginBottom:12, padding:'12px 14px', borderRadius:'var(--r-md)', background: testResult.ok ? 'var(--green-dim)' : 'var(--red-dim)', border:`1px solid ${testResult.ok ? 'rgba(52,209,122,0.2)' : 'rgba(240,80,80,0.2)'}`, fontSize:12, color: testResult.ok ? 'var(--green)' : 'var(--red)' }}>
                {testResult.ok
                  ? `✓ Connected! Found ${testResult.table_count} tables: ${testResult.tables?.slice(0,5).join(', ')}${testResult.table_count > 5 ? '…' : ''}`
                  : `✗ Connection failed: ${testResult.error}`
                }
              </div>
            )}

            <div style={{ display:'flex', gap:8 }}>
              <Btn onClick={handleTestDB} disabled={testing} variant="ghost">
                {testing ? <><Spinner size={12} /> Testing…</> : '⚡ Test Connection'}
              </Btn>
              <Btn onClick={handleSaveDB} disabled={saving}>
                {saving ? <><Spinner size={12} color="#fff" /> Saving…</> : 'Save Connection'}
              </Btn>
              <Btn variant="ghost" onClick={() => { setShowDBForm(false); setEditingIdx(null); setTestResult(null); setDbError('') }}>Cancel</Btn>
            </div>
          </Card>
        )}
      </Section>
    </div>
  )
}
