import React, { useState, useRef } from 'react'
import { deleteConversation } from '../utils/api'
import { APP_NAME } from '../appName'
import Logo from './Logo'

// ── Icons ─────────────────────────────────────────────────────────────────────
const IC = {
  chat:      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>,
  docs:      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>,
  billing:   <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="1" y="4" width="22" height="16" rx="2" ry="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg>,
  usage:     <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>,
  chart:     <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>,
  trend:     <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>,
  alert:     <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>,
  report:    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>,
  plug:      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>,
  settings:  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>,
  chevron:   <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="6 9 12 15 18 9"/></svg>,
  grid:      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>,
}

const ANALYTICS_SUB = [
  { id:'discover',  label:'All Analytics',  icon: IC.grid },
  { id:'reports',   label:'Reports',        icon: IC.report },
]
const PREDICTIONS_SUB = [
  // Forecasting & Anomaly Alerts are shown but intentionally non-clickable for now.
  // The "Predictions" group itself stays expandable.
  { id:'forecast',  label:'Forecasting',    icon: IC.trend,  disabled:true },
  { id:'anomaly',   label:'Anomaly Alerts', icon: IC.alert,  disabled:true },
]

function NavGroup({ label, icon, items, active, setActive, defaultOpen=false }) {
  const [open, setOpen] = useState(defaultOpen || items.some(i => i.id === active))
  const isGroupActive = items.some(i => i.id === active)

  return (
    <div style={{ marginBottom:2 }}>
      <div onClick={() => setOpen(o => !o)} style={{
        display:'flex', alignItems:'center', gap:10, padding:'8px 12px',
        borderRadius:'var(--r-sm)', cursor:'pointer',
        background: isGroupActive && !open ? 'var(--blue-dim)' : 'transparent',
        color: isGroupActive ? 'var(--blue)' : 'var(--text2)',
        fontWeight:500, fontSize:13, userSelect:'none',
      }}>
        <span style={{ flexShrink:0 }}>{icon}</span>
        <span style={{ flex:1 }}>{label}</span>
        <span style={{ transform: open ? 'rotate(180deg)' : 'rotate(0)', transition:'transform .2s', opacity:.5 }}>{IC.chevron}</span>
      </div>
      {open && (
        <div style={{ marginLeft:14, marginTop:2, borderLeft:'1px solid var(--border)', paddingLeft:10 }}>
          {items.map(item => (
            <div key={item.id} onClick={item.disabled ? undefined : () => setActive(item.id)} style={{
              display:'flex', alignItems:'center', gap:9, padding:'7px 10px',
              borderRadius:'var(--r-sm)', cursor: item.disabled ? 'default' : 'pointer', fontSize:13, marginBottom:1,
              background: active===item.id ? 'var(--blue-dim)' : 'transparent',
              color: item.disabled ? 'var(--text3)' : (active===item.id ? 'var(--blue)' : 'var(--text2)'),
              fontWeight: active===item.id ? 600 : 400,
              opacity: item.disabled ? 0.6 : 1,
            }}>
              <span style={{ opacity:.7 }}>{item.icon}</span>
              {item.label}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function ChatNavItem({ active, setActive, conversations, activeConvId, onConvSelect, onCreate, onConvDelete }) {
  const [open, setOpen] = useState(false)
  const [hoverId, setHoverId] = useState(null)
  const [deletingId, setDeletingId] = useState(null)
  const [confirmId, setConfirmId] = useState(null) // conversation pending delete confirmation
  const isChatActive = active === 'chat'

  function fmtDate(iso) {
    if (!iso) return ''
    const d = new Date(iso)
    const now = new Date()
    const diffDays = Math.floor((now - d) / 86400000)
    if (diffDays === 0) return 'Today'
    if (diffDays === 1) return 'Yesterday'
    if (diffDays < 7) return `${diffDays}d ago`
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  }

  function handleDelete(e, convId) {
    e.stopPropagation()
    // Open the in-app confirmation modal instead of the browser confirm() dialog.
    setConfirmId(convId)
  }

  async function doDelete(convId) {
    setConfirmId(null)
    setDeletingId(convId)
    try {
      await deleteConversation(convId)
      onConvDelete?.(convId)
    } catch { /* silent */ }
    finally { setDeletingId(null) }
  }

  return (
    <div style={{ marginBottom: 2 }}>
      {/* Main row */}
      <div style={{ display: 'flex', alignItems: 'center', borderRadius: 'var(--r-sm)', overflow: 'hidden' }}>
        <div
          onClick={() => setActive('chat')}
          style={{
            flex: 1, display: 'flex', alignItems: 'center', gap: 10,
            padding: '8px 12px', cursor: 'pointer',
            background: isChatActive ? 'var(--blue-dim)' : 'transparent',
            color: isChatActive ? 'var(--blue)' : 'var(--text2)',
            fontWeight: isChatActive ? 600 : 400, fontSize: 13,
          }}
        >
          <span style={{ flexShrink: 0 }}>{IC.chat}</span>
          <span style={{ flex: 1 }}>Ask Your Data</span>
        </div>
        {/* Expand/collapse toggle */}
        <div
          onClick={() => setOpen(o => !o)}
          title={open ? 'Hide chat history' : 'Show chat history'}
          style={{
            padding: '8px 10px', cursor: 'pointer',
            color: open ? 'var(--blue)' : 'var(--text3)',
            background: isChatActive ? 'var(--blue-dim)' : 'transparent',
            fontSize: 11,
            transform: open ? 'rotate(180deg)' : 'none',
            transition: 'transform .2s, color .15s',
            flexShrink: 0,
          }}
        >
          {IC.chevron}
        </div>
      </div>

      {/* Conversation list — shown when expanded */}
      {open && (
        <div style={{ marginLeft: 14, marginTop: 2, borderLeft: '1px solid var(--border)', paddingLeft: 10 }}>
          {/* New conversation button */}
          <div
            onClick={() => { setActive('chat'); onCreate?.() }}
            style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '5px 8px', borderRadius: 'var(--r-sm)', cursor: 'pointer',
              color: 'var(--text3)', fontSize: 12, marginBottom: 4,
            }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--blue)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text3)'}
          >
            <span style={{ fontSize: 15, lineHeight: 1 }}>+</span>
            <span>New conversation</span>
          </div>

          {/* Conversation rows */}
          {conversations.length === 0 ? (
            <div style={{ fontSize: 11, color: 'var(--text3)', padding: '4px 8px 8px', lineHeight: 1.5 }}>
              No history yet.<br />Ask your first question.
            </div>
          ) : (
            <div style={{ maxHeight: 280, overflowY: 'auto' }}>
              {conversations.map(conv => {
                const isActive   = conv.id === activeConvId
                const isHovered  = conv.id === hoverId
                const isDeleting = conv.id === deletingId
                return (
                  <div
                    key={conv.id}
                    onClick={() => { setActive('chat'); onConvSelect?.(conv.id) }}
                    onMouseEnter={() => setHoverId(conv.id)}
                    onMouseLeave={() => setHoverId(null)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 6,
                      padding: '5px 8px', borderRadius: 'var(--r-sm)',
                      cursor: 'pointer', marginBottom: 1,
                      background: isActive ? 'var(--blue-dim)' : isHovered ? 'var(--bg2)' : 'transparent',
                      borderLeft: isActive ? '2px solid var(--blue)' : '2px solid transparent',
                    }}
                  >
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontSize: 12, fontWeight: isActive ? 600 : 400,
                        color: isActive ? 'var(--blue)' : 'var(--text2)',
                        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                      }}>
                        {conv.title || 'New conversation'}
                      </div>
                      <div style={{ fontSize: 10, color: 'var(--text3)' }}>
                        {fmtDate(conv.updated_at)}
                      </div>
                    </div>
                    {(isHovered || isDeleting) && (
                      <button
                        onClick={(e) => handleDelete(e, conv.id)}
                        disabled={isDeleting}
                        style={{
                          flexShrink: 0, width: 18, height: 18,
                          borderRadius: 3, background: 'transparent', border: 'none',
                          color: 'var(--text3)', cursor: isDeleting ? 'not-allowed' : 'pointer',
                          fontSize: 14, display: 'flex', alignItems: 'center', justifyContent: 'center',
                          padding: 0,
                        }}
                      >
                        {isDeleting ? '…' : '×'}
                      </button>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* In-app delete confirmation modal (replaces browser window.confirm) */}
      {confirmId && (
        <div
          onClick={() => setConfirmId(null)}
          style={{
            position: 'fixed', inset: 0, zIndex: 1000,
            background: 'rgba(0,0,0,0.45)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            padding: 16,
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: 320, maxWidth: '100%', background: 'var(--bg1)',
              border: '1px solid var(--border)', borderRadius: 'var(--r-lg)',
              padding: '20px 20px 16px', boxShadow: '0 12px 40px rgba(0,0,0,0.35)',
            }}
          >
            <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)', marginBottom: 8 }}>
              Delete conversation?
            </div>
            <div style={{ fontSize: 13, color: 'var(--text3)', lineHeight: 1.5, marginBottom: 18 }}>
              This will permanently delete this conversation and its history. This action cannot be undone.
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button
                onClick={() => setConfirmId(null)}
                style={{
                  padding: '7px 14px', borderRadius: 'var(--r-sm)', fontSize: 13,
                  background: 'var(--bg2)', color: 'var(--text2)',
                  border: '1px solid var(--border)', cursor: 'pointer',
                }}
              >
                Cancel
              </button>
              <button
                onClick={() => doDelete(confirmId)}
                style={{
                  padding: '7px 14px', borderRadius: 'var(--r-sm)', fontSize: 13, fontWeight: 600,
                  background: 'var(--red)', color: '#fff', border: 'none', cursor: 'pointer',
                }}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function NavItem({ id, label, icon, active, setActive, badge }) {
  return (
    <div onClick={() => setActive(id)} style={{
      display:'flex', alignItems:'center', gap:10, padding:'8px 12px',
      borderRadius:'var(--r-sm)', cursor:'pointer', marginBottom:2,
      background: active===id ? 'var(--blue-dim)' : 'transparent',
      color: active===id ? 'var(--blue)' : 'var(--text2)',
      fontWeight: active===id ? 600 : 400, fontSize:13,
    }}>
      <span style={{ flexShrink:0 }}>{icon}</span>
      <span style={{ flex:1 }}>{label}</span>
      {badge && <span style={{ fontSize:10, background:'var(--red)', color:'#fff', borderRadius:99, padding:'1px 6px', fontWeight:600 }}>{badge}</span>}
    </div>
  )
}

export default function Sidebar({ active, setActive, connection, cacheStatus, totalRows, theme, setTheme,
                                   conversations, activeConvId, onConvSelect, onConvCreate, onConvDelete }) {
  const cacheOk      = cacheStatus?.cached
  const cacheBuilding= cacheStatus?.build?.status === 'building'

  return (
    <aside style={{
      width:'var(--sidebar)', background:'var(--bg1)',
      borderRight:'1px solid var(--border)',
      display:'flex', flexDirection:'column', flexShrink:0, overflow:'hidden',
    }}>
      {/* Logo */}
      <div style={{ padding:'16px 14px 12px', borderBottom:'1px solid var(--border)' }}>
        <div style={{ display:'flex', alignItems:'center', gap:9 }}>
          <Logo size={32} />
          <div>
            <div style={{ fontWeight:700, fontSize:14, lineHeight:1.1, display:'flex', alignItems:'center', gap:5 }}>
              {APP_NAME}
              <span style={{ fontSize:9, fontWeight:700, letterSpacing:'0.06em', background:'var(--blue-dim)', color:'var(--blue)', borderRadius:4, padding:'1px 5px', verticalAlign:'middle' }}>BETA</span>
            </div>
            <div style={{ fontSize:10, color:'var(--text3)' }}>AI Analytics</div>
          </div>
        </div>
      </div>

      {/* Connection pill */}
      <div style={{ padding:'10px 14px', borderBottom:'1px solid var(--border)' }}>
        {connection ? (
          <div style={{
            display:'flex', alignItems:'center', gap:8, padding:'8px 11px',
            background:'var(--bg3)', borderRadius:'var(--r-md)',
            border:'1px solid var(--border)', cursor:'pointer',
          }} onClick={() => setActive('connections')}>
            <div style={{ width:7, height:7, borderRadius:'50%', flexShrink:0, background:'var(--green)', boxShadow:'0 0 6px var(--green)' }} />
            <div style={{ flex:1, minWidth:0 }}>
              <div style={{ fontSize:12, fontWeight:600, color:'var(--text)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{connection.display_name || connection.name}</div>
              <div style={{ fontSize:10, color:'var(--text3)', marginTop:1 }}>
                {cacheBuilding
                  ? '⚡ Building cache…'
                  : cacheOk
                    ? `⚡ ${cacheStatus.template_count} analytics ready`
                    : totalRows > 0
                      ? `${totalRows.toLocaleString()} records synced`
                      : 'Connected'}
              </div>
            </div>
            <span style={{ fontSize:10, color:'var(--text3)' }}>›</span>
          </div>
        ) : (
          <div onClick={() => setActive('connections')} style={{
            display:'flex', alignItems:'center', gap:8, padding:'8px 11px',
            background:'var(--amber-dim)', borderRadius:'var(--r-md)',
            border:'1px solid rgba(245,166,35,0.2)', cursor:'pointer',
          }}>
            <div style={{ width:7, height:7, borderRadius:'50%', background:'var(--amber)', flexShrink:0 }} />
            <span style={{ fontSize:12, color:'var(--amber)', fontWeight:500 }}>No data source connected</span>
          </div>
        )}
      </div>

      {/* Navigation */}
      <nav style={{ flex:1, overflowY:'auto', padding:'10px 8px' }}>
        <ChatNavItem
          active={active} setActive={setActive}
          conversations={conversations || []}
          activeConvId={activeConvId}
          onConvSelect={onConvSelect}
          onCreate={onConvCreate}
          onConvDelete={onConvDelete}
        />
        <div style={{ height:1, background:'var(--border)', margin:'8px 4px' }} />
        <div style={{ fontSize:10, color:'var(--text3)', fontWeight:600, textTransform:'uppercase', letterSpacing:'.09em', padding:'4px 12px 6px' }}>Analytics</div>
        <NavGroup label="Analytics" icon={IC.chart}  items={ANALYTICS_SUB}  active={active} setActive={setActive} defaultOpen={true} />
        <NavGroup label="Predictions" icon={IC.trend} items={PREDICTIONS_SUB} active={active} setActive={setActive} />
        <div style={{ height:1, background:'var(--border)', margin:'8px 4px' }} />
        <NavItem id="connections" label="Connections"   icon={IC.plug}     active={active} setActive={setActive} />
        {/* BILLING HIDDEN — <NavItem id="billing" label="Billing" icon={IC.billing} active={active} setActive={setActive} /> */}
        <NavItem id="settings"    label="Settings"      icon={IC.settings} active={active} setActive={setActive} />
      </nav>

      {/* Theme + user bottom row */}
      <div style={{ padding:'10px 14px', borderTop:'1px solid var(--border)' }}>
        {/* Theme toggle */}
        <div onClick={() => setTheme && setTheme(t => t === 'dark' ? 'light' : 'dark')}
          title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'6px 8px', marginBottom:8, borderRadius:'var(--r-sm)', cursor:'pointer', userSelect:'none' }}>
          <div style={{ display:'flex', alignItems:'center', gap:7 }}>
            <span style={{ fontSize:13, lineHeight:1 }}>{theme === 'dark' ? '🌙' : '☀️'}</span>
            <span style={{ fontSize:12, fontWeight:500, color:'var(--text2)' }}>{theme === 'dark' ? 'Dark' : 'Light'}</span>
          </div>
          {/* pill toggle */}
          <div style={{
            width:36, height:20, borderRadius:10, position:'relative', flexShrink:0,
            background: theme === 'dark' ? '#4f8ef7' : 'var(--bg3)',
            border: '1px solid ' + (theme === 'dark' ? '#4f8ef7' : 'var(--border)'),
            transition: 'background .2s, border-color .2s',
          }}>
            <div style={{
              position:'absolute', top:2,
              left: theme === 'dark' ? 17 : 2,
              width:14, height:14, borderRadius:'50%',
              background: theme === 'dark' ? '#fff' : 'var(--text3)',
              transition: 'left .2s',
              boxShadow: '0 1px 3px rgba(0,0,0,0.25)',
            }} />
          </div>
        </div>
        {/* User */}
        <div onClick={() => setActive('settings')} style={{ display:'flex', alignItems:'center', gap:9, cursor:'pointer' }}>
          <div style={{ width:28, height:28, borderRadius:'50%', background:'linear-gradient(135deg,#4f8ef7,#a78bfa)', display:'flex', alignItems:'center', justifyContent:'center', fontWeight:700, fontSize:12, color:'#fff', flexShrink:0 }}>
            {typeof window !== 'undefined' ? (JSON.parse(localStorage.getItem('dm_user')||'{}')?.name?.[0]?.toUpperCase()||'?') : '?'}
          </div>
          <div style={{ flex:1, minWidth:0 }}>
            <div style={{ fontSize:12, fontWeight:500, color:'var(--text)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
              {typeof window !== 'undefined' ? (JSON.parse(localStorage.getItem('dm_user')||'{}')?.name||'') : ''}
            </div>
            <div style={{ fontSize:10, color:'var(--text3)' }}>Settings & account</div>
          </div>
        </div>
      </div>
    </aside>
  )
}
