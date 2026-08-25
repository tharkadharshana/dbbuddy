/**
 * BrandLogo — the brand's own mark, from its partner row.
 *
 * The embed used to render components/Logo.jsx, which is a hardcoded SalesPlay
 * SVG with hardcoded fills. That is correct for the SalesPlay-branded app and
 * wrong for every whitelabel, so the widget renders whatever the brand supplies
 * instead.
 *
 * With no logo configured this falls back to the brand's initial on an accent
 * tile rather than to any particular mark — a fallback that drew one brand's
 * logo would put it in another brand's widget.
 */
export default function BrandLogo({ brand, size = 24, radius = 12, mark = false, style = {} }) {
  const src = mark ? brand?.logoMarkUrl : brand?.logoUrl
  const base = {
    width: size,
    height: size,
    flexShrink: 0,
    borderRadius: radius,
    objectFit: 'contain',
    ...style,
  }

  if (src) {
    return <img src={src} alt={brand?.productName || ''} style={base} />
  }

  const initial = (brand?.productName || '?').trim().charAt(0).toUpperCase()
  return (
    <div
      aria-label={brand?.productName || ''}
      style={{
        ...base,
        background: brand?.accent || '#0058BE',
        color: '#fff',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: Math.max(10, Math.round(size * 0.5)),
        fontWeight: 700,
        lineHeight: 1,
      }}
    >
      {initial}
    </div>
  )
}
