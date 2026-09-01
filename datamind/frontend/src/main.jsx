import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import ErrorBoundary from './ErrorBoundary.jsx'
import { ToastProvider } from './components/Toast.jsx'
import { BrandProvider } from './brand.jsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      <BrandProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </BrandProvider>
    </ErrorBoundary>
  </React.StrictMode>
)
