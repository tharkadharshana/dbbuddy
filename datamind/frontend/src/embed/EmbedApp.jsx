/**
 * EmbedApp.jsx — Root component for the DataMind iframe embed widget.
 *
 * State machine:
 *   loading   → validates partner key via GET /embed/context
 *   error     → invalid/inactive partner key
 *   onboarding → new user (no dm_embed_token in localStorage)
 *   chat      → returning user (valid token found)
 */
import React, { useState, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import './embed.css'
import { embedValidateContext } from './embedApi'
import EmbedOnboarding from './EmbedOnboarding'
import EmbedChat from './EmbedChat'

// ── postMessage helper ────────────────────────────────────────────────────────
export function notifyParent(type, payload = {}) {
  try {
    window.parent.postMessage({ type, ...payload }, '*')
  } catch {
    // No parent frame — running standalone, ignore
  }
}

// ── Root component ────────────────────────────────────────────────────────────
function EmbedApp() {
  const params     = new URLSearchParams(window.location.search)
  const partnerKey = params.get('pk') || ''

  const [state, setState]     = useState('loading')
  const [context, setContext] = useState(null)
  const [errorMsg, setError]  = useState('')

  useEffect(() => {
    if (!partnerKey) {
      setError('No partner key provided. Add ?pk=YOUR_KEY to the iframe URL.')
      setState('error')
      return
    }

    const existingToken = localStorage.getItem('dm_embed_token')

    embedValidateContext(partnerKey)
      .then(ctx => {
        setContext(ctx)
        notifyParent('dm:ready', { partner_name: ctx.partner_name })

        if (existingToken) {
          setState('chat')
          notifyParent('dm:chat_open')
        } else {
          setState('onboarding')
          notifyParent('dm:onboarding_start')
        }
      })
      .catch(err => {
        const msg = err.response?.data?.detail || 'Invalid embed configuration. Check the partner key.'
        setError(msg)
        setState('error')
      })

    // Listen for incoming commands from the parent window
    function handleIncoming(event) {
      if (event.data?.type === 'dm:logout') handleLogout()
    }
    window.addEventListener('message', handleIncoming)
    return () => window.removeEventListener('message', handleIncoming)
  }, [partnerKey])

  function handleOnboardingComplete(token, userData) {
    localStorage.setItem('dm_embed_token', token)
    setState('chat')
    notifyParent('dm:chat_open')
  }

  function handleExpired() {
    localStorage.removeItem('dm_embed_token')
    setState('onboarding')
    notifyParent('dm:onboarding_start')
  }

  function handleLogout() {
    localStorage.removeItem('dm_embed_token')
    setState('onboarding')
    notifyParent('dm:logout')
  }

  if (state === 'loading') {
    return (
      <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:'100%' }}>
        <div style={{ width:20, height:20, border:'2px solid var(--border)', borderTopColor:'var(--blue)', borderRadius:'50%', animation:'spin 0.7s linear infinite' }} />
      </div>
    )
  }

  if (state === 'error') {
    return (
      <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', padding:24, textAlign:'center', gap:12 }}>
        <div style={{ fontSize:32 }}>⚠️</div>
        <div style={{ fontSize:13, color:'var(--text2)', lineHeight:1.7, maxWidth:280 }}>{errorMsg}</div>
      </div>
    )
  }

  if (state === 'onboarding') {
    return (
      <EmbedOnboarding
        context={context}
        partnerKey={partnerKey}
        onComplete={handleOnboardingComplete}
      />
    )
  }

  return (
    <EmbedChat
      context={context}
      onExpired={handleExpired}
      onLogout={handleLogout}
    />
  )
}

// ── Mount ─────────────────────────────────────────────────────────────────────
const root = createRoot(document.getElementById('embed-root'))
root.render(<EmbedApp />)
