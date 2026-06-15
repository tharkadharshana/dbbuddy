/**
 * EmbedSearchBar.jsx — Collapsed "search bar" first impression for the embed
 * widget (?layout=bar). Clicking it tells EmbedApp to expand into the full
 * chat (which is already loading/onboarding in the background).
 */
import React from 'react'

export default function EmbedSearchBar({ context, onExpand }) {
  const accent = context?.branding?.accent_color || '#3B82F6'

  return (
    <div style={{ width:'100%', height:'100%', display:'flex', alignItems:'center', padding:6, boxSizing:'border-box' }}>
      <button
        onClick={onExpand}
        style={{
          display:'flex', alignItems:'center', gap:10, width:'100%', height:'100%',
          background:'#fff', borderRadius:9999, padding:'0 6px 0 14px',
          boxShadow:'0 2px 12px rgba(15,23,42,0.10)', border:'1px solid rgba(15,23,42,0.06)',
          cursor:'pointer', fontFamily:"'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
        }}
      >
        <span style={{
          width:28, height:28, borderRadius:'50%', flexShrink:0,
          background:`${accent}1A`, display:'flex', alignItems:'center', justifyContent:'center', color:accent,
        }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
          </svg>
        </span>
        <span style={{ flex:1, textAlign:'left', fontSize:13, color:'#94A3B8', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
          Ask about your data…
        </span>
        <span style={{
          width:32, height:32, borderRadius:'50%', flexShrink:0,
          background:accent, display:'flex', alignItems:'center', justifyContent:'center', color:'#fff',
        }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
            <path d="M2 21l21-9L2 3v7l15 2-15 2v7z" />
          </svg>
        </span>
      </button>
    </div>
  )
}
