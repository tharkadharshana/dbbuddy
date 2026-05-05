import React from 'react'

// ── Button ────────────────────────────────────────────────────────────────

export function Btn({ children, onClick, variant = 'primary', size = 'md', disabled, style }) {
  const base = {
    display: 'inline-flex', alignItems: 'center', gap: 6,
    fontFamily: 'var(--font-sans)', fontWeight: 500, cursor: disabled ? 'not-allowed' : 'pointer',
    border: 'none', borderRadius: 'var(--radius-md)', transition: 'all 0.15s',
    opacity: disabled ? 0.45 : 1,
  }
  const sizes = { sm: { padding: '5px 12px', fontSize: 12 }, md: { padding: '9px 18px', fontSize: 13 }, lg: { padding: '12px 24px', fontSize: 14 } }
  const variants = {
    primary: { background: 'var(--accent)', color: '#fff' },
    ghost: { background: 'var(--bg-elevated)', color: 'var(--text-secondary)', border: '1px solid var(--border)' },
    danger: { background: 'var(--red-dim)', color: 'var(--red)', border: '1px solid rgba(240,96,96,0.25)' },
  }
  return (
    <button onClick={!disabled ? onClick : undefined} style={{ ...base, ...sizes[size], ...variants[variant], ...style }}>
      {children}
    </button>
  )
}


// ── Card ─────────────────────────────────────────────────────────────────

export function Card({ children, style, className }) {
  return (
    <div className={className} style={{
      background: 'var(--bg-surface)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius-lg)',
      ...style
    }}>
      {children}
    </div>
  )
}


// ── Badge ─────────────────────────────────────────────────────────────────

const badgeColors = {
  green: { background: 'var(--green-dim)', color: 'var(--green)' },
  amber: { background: 'var(--amber-dim)', color: 'var(--amber)' },
  red:   { background: 'var(--red-dim)',   color: 'var(--red)' },
  blue:  { background: 'var(--accent-dim)', color: 'var(--accent)' },
  purple:{ background: 'var(--purple-dim)', color: 'var(--purple)' },
  gray:  { background: 'var(--bg-elevated)', color: 'var(--text-secondary)' },
}

export function Badge({ children, color = 'gray', style }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center',
      padding: '2px 10px', borderRadius: 20,
      fontSize: 11, fontWeight: 500,
      ...badgeColors[color], ...style
    }}>
      {children}
    </span>
  )
}


// ── Spinner ───────────────────────────────────────────────────────────────

export function Spinner({ size = 18, color = 'var(--accent)' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      style={{ animation: 'spin 0.8s linear infinite' }}>
      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
      <circle cx="12" cy="12" r="9" stroke={color} strokeWidth="2.5" strokeDasharray="40 20" strokeLinecap="round"/>
    </svg>
  )
}


// ── LLM Toggle ───────────────────────────────────────────────────────────

export function LLMToggle({ value, onChange }) {
  const opts = ['gemini', 'deepseek']
  return (
    <div style={{ display: 'flex', background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', padding: 3, gap: 2 }}>
      {opts.map(o => (
        <button key={o} onClick={() => onChange(o)} style={{
          padding: '4px 14px', borderRadius: 'var(--radius-sm)', fontSize: 12, fontWeight: 500,
          background: value === o ? 'var(--accent)' : 'transparent',
          color: value === o ? '#fff' : 'var(--text-muted)',
          border: 'none', cursor: 'pointer', transition: 'all 0.15s',
          textTransform: 'capitalize',
        }}>
          {o === 'gemini' ? '✦ Gemini' : '◈ DeepSeek'}
        </button>
      ))}
    </div>
  )
}


// ── Metric Card ───────────────────────────────────────────────────────────

export function MetricCard({ label, value, delta, deltaType }) {
  const deltaColor = deltaType === 'up' ? 'var(--green)' : deltaType === 'down' ? 'var(--red)' : 'var(--text-muted)'
  const deltaPrefix = deltaType === 'up' ? '↑' : deltaType === 'down' ? '↓' : ''
  return (
    <div style={{ background: 'var(--bg-elevated)', borderRadius: 'var(--radius-md)', padding: '14px 16px' }}>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.07em' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, color: 'var(--text-primary)' }}>{value}</div>
      {delta && <div style={{ fontSize: 11, color: deltaColor, marginTop: 4 }}>{deltaPrefix} {delta}</div>}
    </div>
  )
}


// ── Empty State ───────────────────────────────────────────────────────────

export function Empty({ icon, title, subtitle }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '48px 24px', color: 'var(--text-muted)', textAlign: 'center' }}>
      <div style={{ fontSize: 32, marginBottom: 12 }}>{icon}</div>
      <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 4 }}>{title}</div>
      <div style={{ fontSize: 12 }}>{subtitle}</div>
    </div>
  )
}


// ── Section Header ─────────────────────────────────────────────────────────

export function SectionHeader({ title, right }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
      <h2 style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{title}</h2>
      {right}
    </div>
  )
}
