import React, { createContext, useContext, useState, useCallback, useRef } from 'react'

const ToastCtx = createContext(null)

export function useToast() {
  const ctx = useContext(ToastCtx)
  if (!ctx) throw new Error('useToast must be used inside ToastProvider')
  return ctx
}

let _globalToast = null
export function getGlobalToast() { return _globalToast }

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const idRef = useRef(0)

  const dismiss = useCallback((id) => {
    setToasts(t => t.filter(x => x.id !== id))
  }, [])

  const show = useCallback((message, type = 'error', duration = 4500) => {
    const id = ++idRef.current
    setToasts(t => [...t.slice(-4), { id, message, type }])
    setTimeout(() => dismiss(id), duration)
  }, [dismiss])

  const api = {
    error:   (msg) => show(msg, 'error'),
    success: (msg) => show(msg, 'success'),
    info:    (msg) => show(msg, 'info'),
  }

  _globalToast = api

  return (
    <ToastCtx.Provider value={api}>
      {children}
      <ToastStack toasts={toasts} onDismiss={dismiss} />
    </ToastCtx.Provider>
  )
}

const TYPE_STYLE = {
  error:   { bg: 'var(--red-dim, rgba(240,80,80,0.12))',   border: 'rgba(240,80,80,0.3)',   color: 'var(--red, #f05050)',   icon: '✕' },
  success: { bg: 'var(--green-dim, rgba(52,209,122,0.1))', border: 'rgba(52,209,122,0.3)',  color: 'var(--green, #34d17a)', icon: '✓' },
  info:    { bg: 'var(--blue-dim, rgba(79,142,247,0.1))',  border: 'rgba(79,142,247,0.3)',  color: 'var(--blue, #4f8ef7)',  icon: 'ℹ' },
}

function ToastStack({ toasts, onDismiss }) {
  if (!toasts.length) return null
  return (
    <div style={{
      position: 'fixed', bottom: 24, right: 24, zIndex: 9999,
      display: 'flex', flexDirection: 'column', gap: 8, pointerEvents: 'none',
    }}>
      {toasts.map(t => {
        const s = TYPE_STYLE[t.type] || TYPE_STYLE.error
        return (
          <div key={t.id} style={{
            display: 'flex', alignItems: 'flex-start', gap: 10,
            padding: '11px 14px', borderRadius: 10, minWidth: 280, maxWidth: 380,
            background: s.bg, border: `1px solid ${s.border}`,
            boxShadow: '0 4px 20px rgba(0,0,0,0.35)',
            animation: 'toastIn .2s ease', pointerEvents: 'auto',
          }}>
            <span style={{ color: s.color, fontWeight: 700, flexShrink: 0, fontSize: 13 }}>{s.icon}</span>
            <span style={{ fontSize: 13, color: 'var(--text1, #f0f1fa)', lineHeight: 1.5, flex: 1 }}>{t.message}</span>
            <button onClick={() => onDismiss(t.id)} style={{
              background: 'none', border: 'none', cursor: 'pointer', padding: 0,
              color: 'var(--text3, #6b7280)', fontSize: 15, lineHeight: 1, flexShrink: 0,
            }}>×</button>
          </div>
        )
      })}
      <style>{`@keyframes toastIn { from { opacity:0; transform:translateY(8px) } to { opacity:1; transform:translateY(0) } }`}</style>
    </div>
  )
}
