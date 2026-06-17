/**
 * EmbedHistoryDrawer.jsx — slide-out chat history panel for the iframe embed.
 *
 * Reads/writes the same /conversations endpoints as the main app's
 * ConversationSidebar, so history created here shows up there and vice versa.
 */
import React, { useEffect, useState } from 'react'
import { embedListConversations, embedGetConversationMessages } from './embedApi'

function fmtDate(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const now = new Date()
  const diffDays = Math.floor((now - d) / 86400000)
  if (diffDays === 0) return 'Today'
  if (diffDays === 1) return 'Yesterday'
  if (diffDays < 7)  return `${diffDays}d ago`
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export default function EmbedHistoryDrawer({ open, onClose, onSelect, onNewChat, activeConvId, isSalesplay }) {
  const [conversations, setConversations] = useState([])
  const [loading, setLoading] = useState(false)
  const [loadingId, setLoadingId] = useState(null)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    embedListConversations()
      .then(r => setConversations(r.conversations || []))
      .catch(() => setConversations([]))
      .finally(() => setLoading(false))
  }, [open])

  async function handleSelect(convId) {
    setLoadingId(convId)
    try {
      const r = await embedGetConversationMessages(convId)
      const loaded = (r.messages || []).map((m, i) => {
        const snap = m.data_snapshot || null
        return {
          id: m.id || i,
          role: m.role === 'user' ? 'user' : 'ai',
          content: m.content,
          data: snap ? {
            type: 'data',
            columns: snap.columns || [],
            data: snap.rows || [],
            row_count: m.row_count || 0,
          } : null,
          analysis: snap?.analysis || null,
        }
      })
      onSelect(convId, loaded)
    } catch {
      // leave drawer open on failure
    } finally {
      setLoadingId(null)
    }
  }

  const c = isSalesplay
    ? {
        panelBg: '#FFFFFF', heading: '#0F172A', text: '#64748B', text3: '#94A3B8',
        hover: '#F1F5F9', activeBg: 'rgba(59,130,246,0.1)', activeBorder: '#3B82F6',
        border: 'rgba(15,23,42,0.06)', blue: '#3B82F6',
      }
    : {
        panelBg: 'var(--bg1)', heading: 'var(--text)', text: 'var(--text2)', text3: 'var(--text3)',
        hover: 'var(--bg2)', activeBg: 'rgba(79,142,247,0.12)', activeBorder: 'var(--blue)',
        border: 'var(--border)', blue: 'var(--blue)',
      }

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: 'absolute', inset: 0, zIndex: 20,
          background: 'rgba(15,23,42,0.25)',
          opacity: open ? 1 : 0,
          pointerEvents: open ? 'auto' : 'none',
          transition: 'opacity .2s',
        }}
      />
      {/* Panel */}
      <div style={{
        position: 'absolute', top: 0, left: 0, bottom: 0, zIndex: 21,
        width: 'min(82%, 280px)', maxWidth: '100%',
        background: c.panelBg,
        boxShadow: '4px 0 24px rgba(0,0,0,0.12)',
        transform: open ? 'translateX(0)' : 'translateX(-100%)',
        transition: 'transform .25s ease',
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '12px 12px', borderBottom: `1px solid ${c.border}`, flexShrink: 0,
        }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: c.heading, letterSpacing: '.04em', textTransform: 'uppercase' }}>
            Chat history
          </span>
          <button
            onClick={onClose}
            title="Close"
            style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 16, color: c.text3, padding: 4, lineHeight: 1 }}
          >
            ×
          </button>
        </div>

        {/* New chat */}
        <div style={{ padding: '10px 12px', flexShrink: 0 }}>
          <button
            onClick={onNewChat}
            style={{
              width: '100%', padding: '8px 12px', borderRadius: 10,
              background: c.activeBg, border: 'none', cursor: 'pointer',
              color: c.blue, fontSize: 12, fontWeight: 600,
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            }}
          >
            + New chat
          </button>
        </div>

        {/* List */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px 8px' }}>
          {loading ? (
            <div style={{ padding: '24px 14px', textAlign: 'center', color: c.text3, fontSize: 12 }}>
              Loading…
            </div>
          ) : conversations.length === 0 ? (
            <div style={{ padding: '24px 14px', textAlign: 'center', color: c.text3, fontSize: 12, lineHeight: 1.6 }}>
              No conversations yet.<br />Ask your first question to start one.
            </div>
          ) : (
            conversations.map(conv => {
              const isActive = conv.id === activeConvId
              return (
                <div
                  key={conv.id}
                  onClick={() => handleSelect(conv.id)}
                  style={{
                    display: 'flex', alignItems: 'flex-start', gap: 8,
                    padding: '9px 10px', cursor: 'pointer', borderRadius: 8,
                    margin: '2px 0',
                    background: isActive ? c.activeBg : 'transparent',
                    borderLeft: `2px solid ${isActive ? c.activeBorder : 'transparent'}`,
                  }}
                  onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = c.hover }}
                  onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'transparent' }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      fontSize: 12, fontWeight: isActive ? 600 : 400,
                      color: isActive ? c.heading : c.text,
                      whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', lineHeight: 1.4,
                    }}>
                      {conv.title || 'New conversation'}
                    </div>
                    <div style={{ fontSize: 10, color: c.text3, marginTop: 2 }}>
                      {fmtDate(conv.updated_at)}
                      {conv.message_count > 0 && ` · ${Math.floor(conv.message_count / 2)} msg${Math.floor(conv.message_count / 2) !== 1 ? 's' : ''}`}
                    </div>
                  </div>
                  {loadingId === conv.id && (
                    <div style={{ width: 12, height: 12, border: `1.5px solid ${c.border}`, borderTopColor: c.blue, borderRadius: '50%', animation: 'spin 0.7s linear infinite', flexShrink: 0, marginTop: 3 }} />
                  )}
                </div>
              )
            })
          )}
        </div>
      </div>
    </>
  )
}
