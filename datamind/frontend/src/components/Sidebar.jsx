import React from 'react'

const NAV = [
  { id:'discover', label:'Analytics Hub',        icon:'⬡' },
  { id:'query',    label:'Ask a Question',        icon:'⌕' },
  { id:'forecast', label:'Forecasting',           icon:'📈' },
  { id:'anomaly',  label:'Anomaly Detection',     icon:'⚠' },
  { id:'reports',  label:'Report Builder',        icon:'📋' },
  { id:'settings', label:'Settings',              icon:'⚙' },
]

export default function Sidebar({ active, setActive, tables, cacheStatus }) {
  const cacheOk      = cacheStatus?.cached
  const cacheBuilding= cacheStatus?.build?.status === 'building'

  return (
    <aside style={{ width:'var(--sidebar)', background:'var(--bg1)', borderRight:'1px solid var(--border)', display:'flex', flexDirection:'column', flexShrink:0, overflow:'hidden' }}>

      {/* Logo */}
      <div style={{ padding:'16px 16px 12px', borderBottom:'1px solid var(--border)' }}>
        <div style={{ display:'flex', alignItems:'center', gap:9 }}>
          <div style={{ width:30, height:30, borderRadius:8, background:'linear-gradient(135deg,#4f8ef7,#a78bfa)', display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0 }}>
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
              <rect x="2" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.95)"/>
              <rect x="9" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/>
              <rect x="2" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/>
              <rect x="9" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.95)"/>
            </svg>
          </div>
          <div>
            <div style={{ fontWeight:700, fontSize:14, lineHeight:1.1 }}>DataMind</div>
            <div style={{ fontSize:10, color:'var(--text3)' }}>AI Analytics v3</div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav style={{ padding:'10px 8px', flex:1, overflowY:'auto' }}>
        <div style={{ fontSize:10, color:'var(--text3)', textTransform:'uppercase', letterSpacing:'.1em', padding:'0 8px', marginBottom:6, fontWeight:600 }}>Navigation</div>
        {NAV.map(({ id, label, icon }) => {
          const isActive = active === id
          // Show a status dot on Analytics Hub
          const showDot = id === 'discover'
          const dotColor = cacheBuilding ? 'var(--amber)' : cacheOk ? 'var(--green)' : 'var(--text3)'

          return (
            <div key={id} onClick={() => setActive(id)} style={{
              display:'flex', alignItems:'center', gap:9,
              padding:'8px 10px', borderRadius:'var(--r-sm)', marginBottom:1, cursor:'pointer',
              background: isActive ? 'var(--blue-dim)' : 'transparent',
              color: isActive ? 'var(--blue)' : 'var(--text2)',
              fontWeight: isActive ? 600 : 400, fontSize:13,
              borderLeft: isActive ? '2px solid var(--blue)' : '2px solid transparent',
            }}>
              <span style={{ fontSize:14, flexShrink:0 }}>{icon}</span>
              <span style={{ flex:1 }}>{label}</span>
              {showDot && (
                <div style={{
                  width:7, height:7, borderRadius:'50%', flexShrink:0,
                  background: dotColor,
                  boxShadow: cacheOk ? '0 0 6px var(--green)' : cacheBuilding ? '0 0 6px var(--amber)' : 'none',
                  animation: cacheBuilding ? 'pulseGlow 1.2s ease-in-out infinite' : 'none',
                }} />
              )}
            </div>
          )
        })}
      </nav>

      {/* Tables list */}
      <div style={{ padding:'10px 8px', borderTop:'1px solid var(--border)', maxHeight:200, overflowY:'auto' }}>
        <div style={{ fontSize:10, color:'var(--text3)', textTransform:'uppercase', letterSpacing:'.1em', padding:'0 8px', marginBottom:6, fontWeight:600 }}>Tables</div>
        {!tables.length && <div style={{ fontSize:11, color:'var(--text3)', padding:'4px 8px' }}>No DB connected</div>}
        {tables.map(t => (
          <div key={t} style={{ display:'flex', alignItems:'center', gap:7, padding:'4px 8px', borderRadius:'var(--r-sm)', color:'var(--text3)', fontSize:11, fontFamily:'var(--mono)' }}>
            <div style={{ width:5, height:5, borderRadius:'50%', background:'var(--blue)', opacity:.45, flexShrink:0 }} />
            {t}
          </div>
        ))}
      </div>

      {/* DB pill */}
      <div style={{ padding:'10px 16px', borderTop:'1px solid var(--border)', display:'flex', alignItems:'center', gap:7 }}>
        <div style={{
          width:7, height:7, borderRadius:'50%',
          background: tables.length > 0 ? 'var(--green)' : 'var(--red)',
          boxShadow: tables.length > 0 ? '0 0 8px var(--green)' : 'none',
        }} />
        <span style={{ fontSize:11, color:'var(--text3)' }}>
          {tables.length > 0 ? `MySQL · ${tables.length} tables` : 'Not connected'}
        </span>
      </div>
    </aside>
  )
}
