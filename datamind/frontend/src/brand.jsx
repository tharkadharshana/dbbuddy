/**
 * brand.jsx — the standalone app's brand, from the partner row.
 *
 * The app used to read a hardcoded SalesPlay logo and a build-time APP_NAME.
 * One build serves every brand, so both put SalesPlay's identity on every
 * whitelabel's screen. The values now come from GET /brand, which resolves the
 * brand from the Host header the same way login already does.
 *
 * resolveBrand/applyBrandChrome are the widget's, reused unchanged: two
 * resolvers would drift, and the widget's is already the careful one about
 * neutral defaults.
 */
import React, { createContext, useContext, useEffect, useState } from 'react'
import { resolveBrand, applyBrandChrome } from './embed/embedBranding'
import { fetchBrand } from './utils/api'

const BrandContext = createContext(null)

export function BrandProvider({ children }) {
  const [brand, setBrand] = useState(() => resolveBrand(null))

  useEffect(() => {
    let cancelled = false
    fetchBrand()
      .then(data => {
        if (cancelled) return
        // No row for this host: keep the neutral brand rather than guessing.
        const resolved = resolveBrand(data?.branding ? { branding: data.branding } : null)
        setBrand(resolved)
        applyBrandChrome(resolved)
      })
      .catch(() => {})   // offline or backend down: neutral brand still renders
    return () => { cancelled = true }
  }, [])

  return <BrandContext.Provider value={brand}>{children}</BrandContext.Provider>
}

export function useBrand() {
  return useContext(BrandContext) || resolveBrand(null)
}
