import React, { useState, useEffect } from 'react'
import {
  fetchProviders, fetchConnectedProviders,
  validateProviderCreds, connectProvider,
  disconnectProvider, syncProvider, fetchProviderStatus,
  addDBConfig, testDBConnection, getErrorMessage,
} from '../utils/api'
import { Card, Btn, Badge, Spinner, ErrorBox } from '../components/UI'
import { useToast } from '../components/Toast'

// ── Provider card ─────────────────────────────────────────────────────────────
function ProviderCard({ provider, onConnect, canUseDB }) {
  return (
    <div style={{
      background:'var(--bg1)', border:'1px solid var(--border)', borderRadius:'var(--r-lg)',
      padding:'20px', display:'flex', flexDirection:'column', gap:12,
      transition:'border-color .15s',
    }}
      onMouseEnter={e => e.currentTarget.style.borderColor='var(--border2)'}
      onMouseLeave={e => e.currentTarget.style.borderColor='var(--border)'}
    >
      <div style={{ display:'flex', alignItems:'center', gap:12 }}>
        <div style={{ fontSize:32 }}>{provider.logo_emoji}</div>
        <div>
          <div style={{ fontWeight:600, fontSize:14, color:'var(--text)' }}>{provider.display_name}</div>
          <Badge color="gray" style={{ marginTop:3 }}>{provider.category}</Badge>
        </div>
      </div>
      <p style={{ fontSize:12, color:'var(--text3)', lineHeight:1.6 }}>{provider.description}</p>
      <div style={{ display:'flex', gap:8, marginTop:'auto' }}>
        <Btn onClick={onConnect} disabled={canUseDB === false} style={{ flex:1, justifyContent:'center' }}
          title={canUseDB === false ? 'DB record limit reached — upgrade or purchase add-on records to connect' : undefined}>
          Connect
        </Btn>
        <a href={provider.docs_url} target="_blank" rel="noreferrer" style={{ padding:'9px 14px', background:'var(--bg3)', border:'1px solid var(--border)', borderRadius:'var(--r-md)', fontSize:12, color:'var(--text3)', textDecoration:'none', display:'flex', alignItems:'center' }}>Docs ↗</a>
      </div>
    </div>
  )
}

// ── Sync progress bar ─────────────────────────────────────────────────────────
const syncBarStyle = `
  @keyframes syncSlide {
    0%   { transform: translateX(-100%); }
    100% { transform: translateX(400%); }
  }
`

function SyncProgress({ progress }) {
  const pct     = progress?.percent ?? 0
  const msg     = progress?.message || 'Syncing…'
  const rows    = progress?.rows_synced ?? 0
  const elapsed = progress?.elapsed_s ?? 0
  const eta     = pct > 8 ? Math.round((elapsed / pct) * (100 - pct)) : null
  const etaStr  = eta == null ? '' : eta < 60 ? `~${eta}s left` : `~${Math.round(eta/60)}m left`

  return (
    <div style={{ marginTop:6 }}>
      <style>{syncBarStyle}</style>
      <div style={{ height:3, background:'var(--bg3)', borderRadius:2, overflow:'hidden', marginBottom:5 }}>
        {pct > 0
          ? <div style={{ height:'100%', width:`${pct}%`, background:'var(--amber)', borderRadius:2, transition:'width .6s ease' }} />
          : <div style={{ height:'100%', width:'30%', background:'var(--amber)', borderRadius:2, animation:'syncSlide 1.4s linear infinite' }} />
        }
      </div>
      <div style={{ display:'flex', justifyContent:'space-between', fontSize:11, color:'var(--amber)' }}>
        <span style={{ overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', maxWidth:'65%' }}>{msg}</span>
        <span style={{ flexShrink:0 }}>
          {rows > 0 && `${rows.toLocaleString()} records`}{etaStr && ` · ${etaStr}`}
        </span>
      </div>
    </div>
  )
}

// ── Connected row ─────────────────────────────────────────────────────────────
function ConnectedRow({ conn, onDisconnect, onSync, canUseDB }) {
  const [status, setStatus]   = useState(null)
  const [syncingBtn, setSyncingBtn] = useState(false)
  const [pollKey, setPollKey] = useState(0)   // increment to restart poll loop
  const statusRef = React.useRef(null)

  // Self-scheduling poll: polls every 2s while syncing, single fetch otherwise
  useEffect(() => {
    let active = true
    let tid

    async function poll() {
      try {
        const s = await fetchProviderStatus(conn.connection_id)
        if (!active) return
        const wasSyncing = statusRef.current?.status === 'syncing'
        statusRef.current = s
        setStatus(s)
        if (s.status === 'syncing') {
          tid = setTimeout(poll, 2000)
        } else if (wasSyncing) {
          onSync?.()   // sync just finished — refresh parent list + row counts
        }
      } catch {}
    }

    poll()
    return () => { active = false; clearTimeout(tid) }
  }, [conn.connection_id, pollKey])

  async function handleSync() {
    setSyncingBtn(true)
    try {
      await syncProvider(conn.connection_id)
      // Optimistically show syncing, then restart the poll loop
      statusRef.current = null
      setStatus(s => s ? { ...s, status: 'syncing', progress: null } : s)
      setPollKey(k => k + 1)
    } finally { setSyncingBtn(false) }
  }

  const liveStatus = status?.status || conn.last_sync_status
  const isConnected  = liveStatus === 'connected'
  const isSyncing    = liveStatus === 'syncing'

  const rawTs   = status?.last_sync_at || conn.last_sync_at
  const lastSync = rawTs ? new Date(rawTs).toLocaleString() : 'Never'
  const records  = status?.total_rows ?? 0

  return (
    <Card style={{ padding:'16px 18px' }}>
      <div style={{ display:'flex', alignItems:'center', gap:14 }}>
        <div style={{ fontSize:28, flexShrink:0 }}>{conn.logo_emoji || '🔌'}</div>
        <div style={{ flex:1, minWidth:0 }}>
          <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:3 }}>
            <div style={{ fontWeight:600, fontSize:14 }}>{conn.display_name}</div>
            <Badge color={isConnected ? 'green' : isSyncing ? 'amber' : 'gray'}>
              {isSyncing ? 'Syncing…' : isConnected ? 'Connected' : 'Pending'}
            </Badge>
          </div>
          <div style={{ fontSize:11, color:'var(--text3)' }}>
            {records.toLocaleString()} records &nbsp;·&nbsp; Last sync: {lastSync}
          </div>
          {isSyncing && <SyncProgress progress={status?.progress} />}
        </div>
        <div style={{ display:'flex', gap:6, flexShrink:0 }}>
          <Btn size="sm" variant="ghost" onClick={handleSync}
            disabled={syncingBtn || isSyncing || canUseDB === false}
            title={canUseDB === false ? 'DB row limit reached — upgrade or purchase add-on rows to sync' : undefined}>
            {syncingBtn ? <><Spinner size={11} /> Starting…</> : '↺ Sync'}
          </Btn>
          <Btn size="sm" variant="danger" onClick={() => onDisconnect(conn.connection_id)}>Disconnect</Btn>
        </div>
      </div>
    </Card>
  )
}

// ── Connect modal ─────────────────────────────────────────────────────────────
function ConnectModal({ provider, onClose, onConnected }) {
  const [creds, setCreds]         = useState({})
  const [testing, setTesting]     = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [connecting, setConnecting] = useState(false)
  const [error, setError]         = useState('')

  async function handleTest() {
    setTesting(true); setTestResult(null); setError('')
    try {
      const r = await validateProviderCreds(provider.provider_id, creds)
      setTestResult(r)
    } catch(e) { setError(getErrorMessage(e)) }
    finally { setTesting(false) }
  }

  async function handleConnect() {
    setConnecting(true); setError('')
    try {
      await connectProvider(provider.provider_id, creds)
      onConnected()
      onClose()
    } catch(e) { setError(getErrorMessage(e)) }
    finally { setConnecting(false) }
  }

  return (
    <div style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.6)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:1000, backdropFilter:'blur(4px)' }}>
      <div style={{ background:'var(--bg1)', border:'1px solid var(--border)', borderRadius:'var(--r-xl)', padding:28, width:'100%', maxWidth:440, boxShadow:'0 24px 64px rgba(0,0,0,0.6)' }}>
        <div style={{ display:'flex', alignItems:'center', gap:12, marginBottom:20 }}>
          <div style={{ fontSize:36 }}>{provider.logo_emoji}</div>
          <div>
            <div style={{ fontSize:17, fontWeight:700 }}>Connect {provider.display_name}</div>
            <div style={{ fontSize:12, color:'var(--text3)' }}>{provider.description}</div>
          </div>
        </div>

        {provider.credential_fields?.map(field => (
          <div key={field.key} style={{ marginBottom:14 }}>
            <label style={{ fontSize:12, color:'var(--text2)', display:'block', marginBottom:5, fontWeight:500 }}>{field.label}</label>
            <input
              type={field.type === 'password' ? 'password' : 'text'}
              value={creds[field.key] || ''}
              onChange={e => setCreds(c => ({...c, [field.key]: e.target.value}))}
              placeholder={field.placeholder || ''}
              style={{ width:'100%', padding:'9px 12px', fontFamily:'var(--mono)', fontSize:13, borderRadius:'var(--r-md)' }}
            />
            {field.hint && <div style={{ fontSize:11, color:'var(--text3)', marginTop:4 }}>{field.hint}</div>}
          </div>
        ))}

        {error && <div style={{ marginBottom:12 }}><ErrorBox message={error} /></div>}

        {testResult && (
          <div style={{ marginBottom:14, padding:'11px 14px', borderRadius:'var(--r-md)', fontSize:12, background: testResult.ok ? 'var(--green-dim)' : 'var(--red-dim)', border:`1px solid ${testResult.ok ? 'rgba(52,209,122,0.2)' : 'rgba(240,80,80,0.2)'}`, color: testResult.ok ? 'var(--green)' : 'var(--red)' }}>
            {testResult.ok
              ? `✓ Connected to ${testResult.details?.merchant_name || 'account'}`
              : `✗ ${testResult.error}`}
          </div>
        )}

        <div style={{ display:'flex', gap:8 }}>
          <Btn variant="ghost" onClick={onClose}>Cancel</Btn>
          <Btn variant="ghost" onClick={handleTest} disabled={testing}>
            {testing ? <><Spinner size={12} /> Testing…</> : '⚡ Test'}
          </Btn>
          <Btn onClick={handleConnect} disabled={connecting || !testResult?.ok} style={{ flex:1, justifyContent:'center' }}>
            {connecting ? <><Spinner size={12} color="#fff" /> Connecting…</> : 'Connect & Sync →'}
          </Btn>
        </div>
      </div>
    </div>
  )
}

// ── BYODB modal ───────────────────────────────────────────────────────────────
function BYODBModal({ onClose, onConnected }) {
  const [form, setForm] = useState({ name:'My Database', host:'localhost', port:3306, database:'', user:'root', password:'' })
  const [testing, setTesting]     = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [saving, setSaving]       = useState(false)
  const [error, setError]         = useState('')
  const set = (k,v) => setForm(f => ({...f, [k]:v}))

  async function handleTest() {
    setTesting(true); setTestResult(null); setError('')
    try { const r = await testDBConnection(form); setTestResult(r) }
    catch(e) { setError(e.response?.data?.detail || e.message) }
    finally { setTesting(false) }
  }

  async function handleSave() {
    setSaving(true); setError('')
    try { await addDBConfig(form); onConnected(); onClose() }
    catch(e) { setError(e.response?.data?.detail || e.message) }
    finally { setSaving(false) }
  }

  const inp = (k, ph, mono=false, type='text') => (
    <input type={type} value={form[k]} onChange={e => set(k, k==='port'?Number(e.target.value):e.target.value)} placeholder={ph}
      style={{ width:'100%', padding:'9px 12px', fontFamily: mono ? 'var(--mono)' : 'var(--font)', fontSize:13, borderRadius:'var(--r-md)' }} />
  )

  return (
    <div style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.6)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:1000, backdropFilter:'blur(4px)' }}>
      <div style={{ background:'var(--bg1)', border:'1px solid var(--border)', borderRadius:'var(--r-xl)', padding:28, width:'100%', maxWidth:460, boxShadow:'0 24px 64px rgba(0,0,0,0.6)' }}>
        <div style={{ fontSize:17, fontWeight:700, marginBottom:4 }}>🗄 Connect Your MySQL Database</div>
        <div style={{ fontSize:12, color:'var(--text3)', marginBottom:20 }}>Bring your own database and run analytics directly on it.</div>

        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10 }}>
          {[['name','Connection Name','1/-1',false,'text'],['host','Host','',true,'text'],['port','Port','',true,'text'],['database','Database','1/-1',true,'text'],['user','Username','',true,'text'],['password','Password','',false,'password']].map(([k,ph,col,mono,type]) => (
            <div key={k} style={{ gridColumn:col||'auto' }}>
              <label style={{ fontSize:11, color:'var(--text3)', display:'block', marginBottom:4, fontWeight:500 }}>{k.charAt(0).toUpperCase()+k.slice(1)}</label>
              {inp(k, ph, mono, type)}
            </div>
          ))}
        </div>

        {error && <div style={{ marginTop:12 }}><ErrorBox message={error} /></div>}
        {testResult && (
          <div style={{ marginTop:12, padding:'11px 14px', borderRadius:'var(--r-md)', fontSize:12, background: testResult.ok ? 'var(--green-dim)' : 'var(--red-dim)', border:`1px solid ${testResult.ok ? 'rgba(52,209,122,0.2)' : 'rgba(240,80,80,0.2)'}`, color: testResult.ok ? 'var(--green)' : 'var(--red)' }}>
            {testResult.ok ? `✓ Connected — ${testResult.table_count} tables found` : `✗ ${testResult.error}`}
          </div>
        )}

        <div style={{ display:'flex', gap:8, marginTop:16 }}>
          <Btn variant="ghost" onClick={onClose}>Cancel</Btn>
          <Btn variant="ghost" onClick={handleTest} disabled={testing}>{testing ? <><Spinner size={12} /> Testing…</> : '⚡ Test'}</Btn>
          <Btn onClick={handleSave} disabled={saving || !testResult?.ok} style={{ flex:1, justifyContent:'center' }}>
            {saving ? <><Spinner size={12} color="#fff" /> Saving…</> : 'Save & Connect →'}
          </Btn>
        </div>
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function ConnectionsPage({ onConnectionChange, sub }) {
  const toast = useToast()
  const [providers, setProviders]     = useState([])
  const [connected, setConnected]     = useState([])
  const [loading, setLoading]         = useState(true)
  const [modal, setModal]             = useState(null) // null | provider obj | 'byodb'
  const [tab, setTab]                 = useState('connected') // 'connected' | 'browse'

  const load = async () => {
    setLoading(true)
    // Fetch independently so one failure doesn't blank out the other
    const [p, c] = await Promise.allSettled([fetchProviders(), fetchConnectedProviders()])
    if (p.status === 'fulfilled') setProviders(p.value.providers || [])
    if (c.status === 'fulfilled') setConnected(c.value.connections || [])
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  async function handleDisconnect(cid) {
    if (!window.confirm('Disconnect this integration? Synced data will be deleted.')) return
    try {
      await disconnectProvider(cid)
      load()
      onConnectionChange?.()
    } catch(e) {
      toast.error(getErrorMessage(e, 'Failed to disconnect. Please try again.'))
    }
  }

  const availableProviders = providers.filter(p => !connected.some(c => c.provider_id === p.provider_id))
    // NOTE: External integrations other than Salesplay POS are temporarily hidden.
    // Right now the only available integration is Salesplay POS. More integrations
    // (e.g. Loyverse POS, Bring Your Own Database) will be surfaced here in the future.
    .filter(p => /salesplay/i.test(p.provider_id))
  const canUseDB = sub ? sub.can_use_db : true  // default true while sub is loading

  return (
    <div style={{ maxWidth:820, padding:'24px 24px 60px' }}>
      <div style={{ marginBottom:24 }}>
        <div style={{ fontSize:20, fontWeight:700, marginBottom:4 }}>Data Sources</div>
        <div style={{ fontSize:13, color:'var(--text3)' }}>Connect your business tools or your own database to unlock AI analytics.</div>
      </div>

      {/* Tab bar */}
      <div style={{ display:'flex', gap:4, background:'var(--bg2)', borderRadius:'var(--r-md)', padding:4, marginBottom:20, width:'fit-content' }}>
        {[['connected','Connected'],['browse','Browse Integrations']].map(([id,label]) => (
          <button key={id} onClick={() => setTab(id)} style={{ padding:'6px 18px', borderRadius:'var(--r-sm)', fontSize:13, fontWeight:500, background:tab===id?'var(--bg1)':'transparent', color:tab===id?'var(--text)':'var(--text3)', border:`1px solid ${tab===id?'var(--border)':'transparent'}`, cursor:'pointer' }}>{label}</button>
        ))}
      </div>

      {loading && <div style={{ padding:32, textAlign:'center', color:'var(--text3)' }}><Spinner size={24} /></div>}

      {/* Connected tab */}
      {!loading && tab === 'connected' && (
        <>
          {!canUseDB && connected.length > 0 && (
            <div style={{ marginBottom:12, padding:'10px 14px', borderRadius:'var(--r-md)', fontSize:13, color:'var(--red)', background:'rgba(239,68,68,0.08)', border:'1px solid rgba(239,68,68,0.25)' }}>
              DB record limit reached — syncing is paused. Purchase add-on records or upgrade to resume.
            </div>
          )}
          {connected.length === 0 ? (
            <div style={{ textAlign:'center', padding:'48px 24px', color:'var(--text3)' }}>
              <div style={{ fontSize:40, marginBottom:12, opacity:.3 }}>🔌</div>
              <div style={{ fontSize:15, fontWeight:600, color:'var(--text2)', marginBottom:8 }}>No connections yet</div>
              <div style={{ fontSize:13, marginBottom:20 }}>Connect a data source to start getting AI-powered insights.</div>
              <Btn onClick={() => setTab('browse')}>Browse Integrations →</Btn>
            </div>
          ) : (
            <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
              {connected.map(c => (
                <ConnectedRow key={c.connection_id} conn={c} onDisconnect={handleDisconnect}
                  onSync={() => { load(); onConnectionChange?.() }} canUseDB={canUseDB} />
              ))}
            </div>
          )}
        </>
      )}

      {/* Browse tab */}
      {!loading && tab === 'browse' && (
        <>
          {!canUseDB && (
            <div style={{ marginBottom:16, padding:'10px 14px', borderRadius:'var(--r-md)', fontSize:13, color:'var(--red)', background:'rgba(239,68,68,0.08)', border:'1px solid rgba(239,68,68,0.25)' }}>
              You've reached your DB record limit. Connecting or syncing integrations is paused. You can still read and export your existing data. <strong>Purchase add-on records or upgrade your plan</strong> to continue.
            </div>
          )}
          {/* BYODB option — temporarily hidden. Bring Your Own Database will return as a
              future integration; right now the only available integration is Salesplay POS. */}
          {/*
          <div style={{ marginBottom:20, padding:'16px 18px', background:'var(--bg1)', border:'1px solid var(--border)', borderRadius:'var(--r-lg)', display:'flex', alignItems:'center', justifyContent:'space-between', gap:16 }}>
            <div style={{ display:'flex', alignItems:'center', gap:14 }}>
              <div style={{ fontSize:28 }}>🗄</div>
              <div>
                <div style={{ fontWeight:600, fontSize:14 }}>Bring Your Own Database</div>
                <div style={{ fontSize:12, color:'var(--text3)', marginTop:2 }}>Connect directly to your MySQL database</div>
              </div>
            </div>
            <Btn onClick={() => setModal('byodb')}>Connect</Btn>
          </div>
          */}

          {/* External Integrations — only Salesplay POS is available for now.
              More integrations (e.g. Loyverse POS) will be added here in the future. */}
          <div style={{ fontSize:11, color:'var(--text3)', fontWeight:600, textTransform:'uppercase', letterSpacing:'.08em', marginBottom:12 }}>External Integrations</div>
          {providers.length === 0 ? (
            <div style={{ padding:'24px 0', color:'var(--text3)', fontSize:13 }}>
              No integrations available. Check your backend connection.
            </div>
          ) : availableProviders.length === 0 ? (
            <div style={{ fontSize:13, color:'var(--text3)', padding:'20px 0' }}>
              All available integrations are already connected.
            </div>
          ) : (
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:14 }}>
              {availableProviders.map(p => (
                <ProviderCard key={p.provider_id} provider={p} onConnect={() => setModal(p)} canUseDB={canUseDB} />
              ))}
            </div>
          )}
          <div style={{ fontSize:12, color:'var(--text3)', marginTop:16, fontStyle:'italic' }}>
            More integrations are on the way. Right now, Salesplay POS is the only available integration.
          </div>
        </>
      )}

      {/* Modals */}
      {modal && modal !== 'byodb' && (
        <ConnectModal provider={modal} onClose={() => setModal(null)} onConnected={() => { load(); onConnectionChange?.() }} />
      )}
      {modal === 'byodb' && (
        <BYODBModal onClose={() => setModal(null)} onConnected={() => { load(); onConnectionChange?.() }} />
      )}
    </div>
  )
}
