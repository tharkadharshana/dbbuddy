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
 *
 * `size` is a HEIGHT, not a box. A brand may supply a square mark or a wide
 * wordmark, and forcing either into a square crushes the other: a 4:1 wordmark
 * in a 24px square renders as a ~24x6 sliver. The image keeps its own aspect
 * ratio and only its height is constrained. Pass `square` for the placements
 * that genuinely need a fixed tile.
 */
export default function BrandLogo({ brand, size = 24, radius = 12, mark = false, square = false, style = {} }) {
  const src = mark ? brand?.logoMarkUrl : brand?.logoUrl
  const base = {
    width: square ? size : 'auto',
    height: size,
    maxWidth: '100%',
    flexShrink: 0,
    borderRadius: radius,
    objectFit: 'contain',
    ...style,
  }

  if (src) {
    return <img src={src} alt={brand?.productName || ''} style={base} />
  }

  // The initial tile is always a rounded square — it is a letter on an accent
  // block, not artwork, so the caller's radius (tuned for whatever logo that
  // brand supplies, often 0 for a wordmark) must not flatten it.
  const initial = (brand?.productName || '?').trim().charAt(0).toUpperCase()
  return (
    <div
      aria-label={brand?.productName || ''}
      style={{
        ...base,
        width: size,
        borderRadius: Math.max(radius, Math.round(size * 0.25)),
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
