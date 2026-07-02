/**
 * EmbedFeedbackModal.jsx — 5-star rating + comment prompt shown when the
 * user closes/minimizes the embed widget.
 */
import React, { useState } from 'react'

const STAR_LABELS = ['Poor', 'Fair', 'Good', 'Great', 'Excellent']

export default function EmbedFeedbackModal({ onSubmit, onRemindLater }) {
  const [rating, setRating] = useState(0)
  const [hovered, setHovered] = useState(0)
  const [comment, setComment] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit() {
    if (!rating || submitting) return
    setSubmitting(true)
    await onSubmit(rating, comment)
    setSubmitting(false)
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 1000,
      background: 'rgba(15,23,42,0.45)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: 16,
    }}>
      <div style={{
        background: 'var(--bg, #fff)', borderRadius: 16, padding: 20,
        width: '100%', maxWidth: 320, boxShadow: '0 12px 32px rgba(0,0,0,0.2)',
        display: 'flex', flexDirection: 'column', gap: 14,
      }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)' }}>
            Before you go — how was your experience?
          </div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 4 }}>
            Your feedback helps us improve this AI tool.
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'center', gap: 6 }}>
          {[1, 2, 3, 4, 5].map(n => (
            <button
              key={n}
              type="button"
              onClick={() => setRating(n)}
              onMouseEnter={() => setHovered(n)}
              onMouseLeave={() => setHovered(0)}
              title={STAR_LABELS[n - 1]}
              style={{
                background: 'none', border: 'none', cursor: 'pointer', padding: 2,
                fontSize: 28, lineHeight: 1,
                color: (hovered || rating) >= n ? '#f5b301' : 'var(--border2, #ccc)',
              }}
            >
              ★
            </button>
          ))}
        </div>

        <textarea
          value={comment}
          onChange={e => setComment(e.target.value)}
          placeholder="What do you think about this AI tool? (optional)"
          rows={3}
          style={{
            width: '100%', resize: 'none', borderRadius: 10,
            border: '1px solid var(--border)', padding: 8,
            fontSize: 13, fontFamily: 'inherit', color: 'var(--text)',
            background: 'transparent', boxSizing: 'border-box',
          }}
        />

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button
            type="button"
            onClick={onRemindLater}
            disabled={submitting}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              fontSize: 13, color: 'var(--text3)', padding: '8px 10px',
            }}
          >
            Remind me later
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!rating || submitting}
            style={{
              background: rating ? 'var(--blue)' : 'var(--border2, #ccc)',
              border: 'none', borderRadius: 20, cursor: rating ? 'pointer' : 'default',
              fontSize: 13, fontWeight: 600, color: '#fff', padding: '8px 16px',
            }}
          >
            {submitting ? 'Sending…' : 'Submit'}
          </button>
        </div>
      </div>
    </div>
  )
}
