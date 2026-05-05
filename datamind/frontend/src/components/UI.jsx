import React from 'react'

// ── Button ────────────────────────────────────────────────────────────────

export function Btn({ children, onClick, variant = 'primary', size = 'md', disabled, style }) {
  const base = {
    display: 'inline-flex', alignItems: 'center', gap: 6,
    fontFamily: 'var(--font-sans)', fontWeight: 500, cursor: disabled ? 'not-allowed' : 'pointer',
    border: 'none', borderRadius: 'var(--border-radius-md)', transition: 'all 0.15s',
    opacity: disabled ? 0.45 : 1,
  }
  const sizes = { 
    sm: { padding: '5px 12px', fontSize: 12 }, 
    md: { padding: '9px 18px', fontSize: 13 }, 
    lg: { padding: '12px 24px', fontSize: 14 } 
  }
  const variants = {
    primary: { background: '#1a1a2e', color: '#e8e8f0' },
    ghost: { background: 'var(--color-background-secondary)', color: 'var(--color-text-secondary)', border: '0.5px solid var(--color-border-tertiary)' },
    danger: { background: '#FCEBEB', color: '#A32D2D', border: '0.5px solid rgba(163,45,45,0.1)' },
  }
  return (
    <button onClick={!disabled ? onClick : undefined} style={{ ...base, ...sizes[size], ...variants[variant], ...style }}>
      {children}
    </button>
  )
}


// ── Card ─────────────────────────────────────────────────────────────────

export function Card({ children, style, className, onClick }) {
  return (
    <div className={className} onClick={onClick} style={{
      background: 'var(--color-background-primary)',
      border: '0.5px solid var(--color-border-tertiary)',
      borderRadius: 'var(--border-radius-lg)',
      ...style
    }}>
      {children}
    </div>
  )
}


// ── Badge ─────────────────────────────────────────────────────────────────

const badgeColors = {
  green: { background: '#eaf3de', color: '#3B6D11' },
  amber: { background: '#FAEEDA', color: '#854F0B' },
  red:   { background: '#FCEBEB', color: '#A32D2D' },
  blue:  { background: '#E6F1FB', color: '#185FA5' },
  purple:{ background: '#f0f0f8', color: '#534AB7' },
  gray:  { background: 'var(--color-background-secondary)', color: 'var(--color-text-secondary)' },
}

export function Badge({ children, color = 'gray', style }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center',
      padding: '3px 10px', borderRadius: 20,
      fontSize: 11, fontWeight: 500,
      ...badgeColors[color], ...style
    }}>
      {children}
    </span>
  )
}


// ── Spinner ───────────────────────────────────────────────────────────────

export function Spinner({ size = 18, color = '#1a1a2e' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      style={{ animation: 'spin 0.8s linear infinite' }}>
      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
      <circle cx="12" cy="12" r="9" stroke={color} strokeWidth="2.5" strokeDasharray="40 20" strokeLinecap="round"/>
    </svg>
  )
}


// ── Metric Card ───────────────────────────────────────────────────────────

export function MetricCard({ label, value, delta, deltaType }) {
  const deltaColor = deltaType === 'up' ? '#3B6D11' : deltaType === 'down' ? '#A32D2D' : 'var(--color-text-tertiary)'
  const deltaPrefix = deltaType === 'up' ? '+' : deltaType === 'down' ? '−' : ''
  return (
    <div style={{ background: 'var(--color-background-secondary)', borderRadius: 'var(--border-radius-md)', padding: '12px 14px' }}>
      <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 500, color: 'var(--color-text-primary)' }}>{value}</div>
      {delta && <div style={{ fontSize: 11, color: deltaColor, marginTop: 4 }}>{deltaPrefix}{delta}</div>}
    </div>
  )
}


// ── Empty State ───────────────────────────────────────────────────────────

export function Empty({ icon, title, subtitle }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '48px 24px', color: 'var(--color-text-tertiary)', textAlign: 'center' }}>
      <div style={{ fontSize: 32, marginBottom: 12 }}>{icon}</div>
      <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--color-text-secondary)', marginBottom: 4 }}>{title}</div>
      <div style={{ fontSize: 12 }}>{subtitle}</div>
    </div>
  )
}


// ── Section Header ─────────────────────────────────────────────────────────

export function SectionHeader({ title, right }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
      <h2 style={{ fontSize: 13, fontWeight: 500, color: 'var(--color-text-secondary)' }}>{title}</h2>
      {right}
    </div>
  )
}
