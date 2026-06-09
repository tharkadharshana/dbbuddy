import React from 'react'

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, info) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (!this.state.hasError) return this.props.children

    return (
      <div style={{
        display: 'flex', flexDirection: 'column', alignItems: 'center',
        justifyContent: 'center', height: '100vh', padding: 32, textAlign: 'center',
        background: 'var(--bg0, #0f1117)', color: 'var(--text1, #f0f1fa)',
      }}>
        <div style={{ fontSize: 40, marginBottom: 16 }}>⚠️</div>
        <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 8 }}>Something went wrong</div>
        <div style={{ fontSize: 13, color: 'var(--text3, #6b7280)', maxWidth: 420, marginBottom: 24 }}>
          An unexpected error occurred. Your data is safe — please reload the page to continue.
        </div>
        <button
          onClick={() => window.location.reload()}
          style={{
            padding: '9px 20px', fontSize: 13, fontWeight: 500, borderRadius: 8,
            background: 'var(--blue, #4f8ef7)', color: '#fff', border: 'none', cursor: 'pointer',
          }}
        >
          Reload page
        </button>
        {import.meta.env.DEV && this.state.error && (
          <pre style={{
            marginTop: 24, padding: 12, borderRadius: 8, fontSize: 11, textAlign: 'left',
            background: 'var(--bg2, #1a1c2e)', color: 'var(--red, #f05050)',
            maxWidth: 600, overflow: 'auto', maxHeight: 200,
          }}>
            {this.state.error.toString()}
          </pre>
        )}
      </div>
    )
  }
}
