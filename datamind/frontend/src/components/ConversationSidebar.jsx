import React, { useState } from 'react'
import { deleteConversation } from '../utils/api'

export default function ConversationSidebar({
  conversations = [],
  activeConvId,
  onSelect,
  onCreate,
  onDelete,
}) {
  const [hoverId, setHoverId]     = useState(null)
  const [deletingId, setDeletingId] = useState(null)

  async function handleDelete(e, convId) {
    e.stopPropagation()
    if (!window.confirm('Delete this conversation?')) return
    setDeletingId(convId)
    try {
      await deleteConversation(convId)
      onDelete(convId)
    } catch { /* silent */ }
    finally { setDeletingId(null) }
  }

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

  return (
    <div style={{
      width: 220,
      flexShrink: 0,
      display: 'flex',
      flexDirection: 'column',
      borderRight: '1px solid var(--border)',
      background: 'var(--bg1)',
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '14px 12px 10px',
        borderBottom: '1px solid var(--border)',
        flexShrink: 0,
      }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text3)', letterSpacing: '.06em', textTransform: 'uppercase' }}>
          Chats
        </span>
        <button
          onClick={onCreate}
          title="New conversation"
          style={{
            width: 26, height: 26, borderRadius: 7,
            background: 'var(--bg2)', border: '1px solid var(--border)',
            color: 'var(--text2)', fontSize: 18, cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            lineHeight: 1, flexShrink: 0,
          }}
        >
          +
        </button>
      </div>

      {/* List */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '6px 0' }}>
        {conversations.length === 0 ? (
          <div style={{ padding: '24px 14px', textAlign: 'center', color: 'var(--text3)', fontSize: 12, lineHeight: 1.6 }}>
            No conversations yet.<br />Ask your first question to start one.
          </div>
        ) : (
          conversations.map(conv => {
            const isActive  = conv.id === activeConvId
            const isHovered = conv.id === hoverId
            const isDeleting = conv.id === deletingId

            return (
              <div
                key={conv.id}
                onClick={() => onSelect(conv.id)}
                onMouseEnter={() => setHoverId(conv.id)}
                onMouseLeave={() => setHoverId(null)}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 8,
                  padding: '8px 10px',
                  cursor: 'pointer',
                  borderRadius: 8,
                  margin: '1px 6px',
                  background: isActive
                    ? 'rgba(79,142,247,0.12)'
                    : isHovered ? 'var(--bg2)' : 'transparent',
                  borderLeft: isActive ? '2px solid var(--blue)' : '2px solid transparent',
                  transition: 'background .1s',
                  position: 'relative',
                }}
              >
                {/* Active dot */}
                {isActive && (
                  <div style={{
                    width: 6, height: 6, borderRadius: '50%',
                    background: 'var(--blue)', flexShrink: 0, marginTop: 5,
                  }} />
                )}

                {/* Text block */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontSize: 12, fontWeight: isActive ? 600 : 400,
                    color: isActive ? 'var(--text)' : 'var(--text2)',
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                    lineHeight: 1.4,
                  }}>
                    {conv.title || 'New conversation'}
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 2 }}>
                    {fmtDate(conv.updated_at)}
                    {conv.message_count > 0 && ` · ${Math.floor(conv.message_count / 2)} msg${Math.floor(conv.message_count / 2) !== 1 ? 's' : ''}`}
                  </div>
                </div>

                {/* Delete button — visible on hover */}
                {(isHovered || isDeleting) && (
                  <button
                    onClick={(e) => handleDelete(e, conv.id)}
                    disabled={isDeleting}
                    title="Delete conversation"
                    style={{
                      flexShrink: 0,
                      width: 20, height: 20,
                      borderRadius: 4,
                      background: 'transparent',
                      border: 'none',
                      color: 'var(--text3)',
                      cursor: isDeleting ? 'not-allowed' : 'pointer',
                      fontSize: 14,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      padding: 0,
                    }}
                  >
                    {isDeleting ? '…' : '×'}
                  </button>
                )}
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
