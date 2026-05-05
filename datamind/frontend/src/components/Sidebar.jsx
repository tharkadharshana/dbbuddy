import React from 'react'

const navItems = [
  { id: 'query',    label: 'Query',      icon: IconSearch },
  { id: 'forecast', label: 'Forecast',   icon: IconTrend },
  { id: 'anomaly',  label: 'Anomalies',  icon: IconAlert },
  { id: 'reports',  label: 'Reports',    icon: IconDoc },
]

function IconSearch() {
  return <svg viewBox="0 0 16 16" fill="none" width="14" height="14"><circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.2"/><path d="M10.5 10.5L13 13" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>
}
function IconTrend() {
  return <svg viewBox="0 0 16 16" fill="none" width="14" height="14"><polyline points="2,12 5,8 8,10 11,5 14,7" stroke="currentColor" strokeWidth="1.2" fill="none" strokeLinecap="round" strokeLinejoin="round"/></svg>
}
function IconAlert() {
  return <svg viewBox="0 0 16 16" fill="none" width="14" height="14"><path d="M8 2L14 13H2L8 2Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round"/><line x1="8" y1="6" x2="8" y2="9" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/><circle cx="8" cy="11" r="0.7" fill="currentColor"/></svg>
}
function IconDoc() {
  return <svg viewBox="0 0 16 16" fill="none" width="14" height="14"><rect x="3" y="2" width="10" height="12" rx="1.5" stroke="currentColor" strokeWidth="1.2"/><line x1="5.5" y1="5.5" x2="10.5" y2="5.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/><line x1="5.5" y1="8" x2="10.5" y2="8" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/><line x1="5.5" y1="10.5" x2="8.5" y2="10.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>
}

export default function Sidebar({ active, setActive, tables }) {
  return (
    <aside style={{
      width: 200,
      background: 'var(--color-background-primary)',
      borderRight: '0.5px solid var(--color-border-tertiary)',
      padding: '16px 0',
      display: 'flex',
      flexDirection: 'column',
      flexShrink: 0,
    }}>
      <div style={{ padding: '0 12px', marginBottom: 20 }}>
        <div style={{ fontSize: 10, fontWeight: 500, color: 'var(--color-text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em', padding: '0 8px', marginBottom: 6 }}>Navigation</div>
        {navItems.map(({ id, label, icon: Icon }) => (
          <div
            key={id}
            onClick={() => setActive(id)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '7px 8px',
              borderRadius: 'var(--border-radius-md)',
              fontSize: 13,
              color: active === id ? '#e8e8f0' : 'var(--color-text-secondary)',
              background: active === id ? '#1a1a2e' : 'transparent',
              cursor: 'pointer',
              transition: 'all 0.1s',
            }}
            onMouseEnter={(e) => {
              if (active !== id) {
                e.currentTarget.style.background = 'var(--color-background-secondary)';
                e.currentTarget.style.color = 'var(--color-text-primary)';
              }
            }}
            onMouseLeave={(e) => {
              if (active !== id) {
                e.currentTarget.style.background = 'transparent';
                e.currentTarget.style.color = 'var(--color-text-secondary)';
              }
            }}
          >
            <Icon />
            {label}
          </div>
        ))}
      </div>

      <div style={{ padding: '0 12px' }}>
        <div style={{ fontSize: 10, fontWeight: 500, color: 'var(--color-text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em', padding: '0 8px', marginBottom: 6 }}>Tables</div>
        {tables.map(t => (
          <div key={t} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 8px', borderRadius: 'var(--border-radius-md)', fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--color-text-secondary)', cursor: 'pointer' }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = 'var(--color-background-secondary)';
              e.currentTarget.style.color = 'var(--color-text-primary)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
              e.currentTarget.style.color = 'var(--color-text-secondary)';
            }}
          >
            <div style={{ width: 5, height: 5, background: '#378ADD', borderRadius: '50%', flexShrink: 0 }}></div>
            {t}
          </div>
        ))}
      </div>
    </aside>
  )
}
