import React from 'react'

const navItems = [
  { id: 'query',    label: 'Query',      icon: IconSearch },
  { id: 'forecast', label: 'Forecast',   icon: IconTrend },
  { id: 'anomaly',  label: 'Anomalies',  icon: IconAlert },
  { id: 'reports',  label: 'Reports',    icon: IconDoc },
]

function IconSearch() {
  return <svg width="15" height="15" viewBox="0 0 16 16" fill="none"><circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.4"/><path d="M10.5 10.5L13 13" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>
}
function IconTrend() {
  return <svg width="15" height="15" viewBox="0 0 16 16" fill="none"><polyline points="2,12 5,8 8,10 11,5 14,7" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" strokeLinejoin="round"/></svg>
}
function IconAlert() {
  return <svg width="15" height="15" viewBox="0 0 16 16" fill="none"><path d="M8 2L14 13H2L8 2Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/><line x1="8" y1="6" x2="8" y2="9" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/><circle cx="8" cy="11" r="0.8" fill="currentColor"/></svg>
}
function IconDoc() {
  return <svg width="15" height="15" viewBox="0 0 16 16" fill="none"><rect x="3" y="2" width="10" height="12" rx="1.5" stroke="currentColor" strokeWidth="1.4"/><line x1="5.5" y1="5.5" x2="10.5" y2="5.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/><line x1="5.5" y1="8" x2="10.5" y2="8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/><line x1="5.5" y1="10.5" x2="8.5" y2="10.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>
}
function IconTable() {
  return <svg width="10" height="10" viewBox="0 0 12 12" fill="none"><rect x="1" y="1" width="10" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.2"/><line x1="1" y1="4" x2="11" y2="4" stroke="currentColor" strokeWidth="1.2"/><line x1="5" y1="4" x2="5" y2="11" stroke="currentColor" strokeWidth="1.2"/></svg>
}

export default function Sidebar({ active, setActive, tables }) {
  return (
    <aside style={{
      width: 210,
      background: 'var(--bg-surface)',
      borderRight: '1px solid var(--border)',
      display: 'flex',
      flexDirection: 'column',
      flexShrink: 0,
    }}>
      {/* Logo */}
      <div style={{ padding: '18px 16px 14px', borderBottom: '1px solid var(--border)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 30, height: 30, background: 'linear-gradient(135deg, var(--accent), var(--purple))', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <rect x="2" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.9)"/>
              <rect x="9" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/>
              <rect x="2" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/>
              <rect x="9" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.9)"/>
            </svg>
          </div>
          <span style={{ fontWeight: 600, fontSize: 15, color: 'var(--text-primary)' }}>DataMind</span>
        </div>
      </div>

      {/* Nav */}
      <nav style={{ padding: '12px 8px' }}>
        <div style={{ fontSize: 10, fontWeight: 500, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em', padding: '0 8px', marginBottom: 6 }}>Workspace</div>
        {navItems.map(({ id, label, icon: Icon }) => (
          <div key={id} onClick={() => setActive(id)} style={{
            display: 'flex', alignItems: 'center', gap: 9,
            padding: '8px 10px', borderRadius: 'var(--radius-sm)',
            cursor: 'pointer', marginBottom: 1,
            background: active === id ? 'var(--accent-dim)' : 'transparent',
            color: active === id ? 'var(--accent)' : 'var(--text-secondary)',
            fontWeight: active === id ? 500 : 400,
            fontSize: 13,
            borderLeft: active === id ? '2px solid var(--accent)' : '2px solid transparent',
          }}>
            <Icon />
            {label}
          </div>
        ))}
      </nav>

      {/* Tables */}
      <div style={{ padding: '12px 8px', borderTop: '1px solid var(--border)', marginTop: 'auto', maxHeight: 260, overflowY: 'auto' }}>
        <div style={{ fontSize: 10, fontWeight: 500, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em', padding: '0 8px', marginBottom: 8 }}>Tables</div>
        {tables.length === 0 && (
          <div style={{ fontSize: 11, color: 'var(--text-muted)', padding: '4px 8px' }}>No DB connected</div>
        )}
        {tables.map(t => (
          <div key={t} style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '5px 8px', borderRadius: 'var(--radius-sm)', color: 'var(--text-muted)', fontSize: 12, fontFamily: 'var(--font-mono)', cursor: 'default' }}>
            <IconTable />
            {t}
          </div>
        ))}
      </div>

      {/* DB status */}
      <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 6 }}>
        <div style={{ width: 6, height: 6, borderRadius: '50%', background: tables.length > 0 ? 'var(--green)' : 'var(--red)', boxShadow: tables.length > 0 ? '0 0 6px var(--green)' : 'none' }} />
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{tables.length > 0 ? `MySQL · ${tables.length} tables` : 'Not connected'}</span>
      </div>
    </aside>
  )
}
