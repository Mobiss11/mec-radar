/** Format number with K/M/B suffixes */
export function formatCompact(value: number | null | undefined): string {
  if (value == null) return "—"
  if (Math.abs(value) >= 1e9) return `${(value / 1e9).toFixed(1)}B`
  if (Math.abs(value) >= 1e6) return `${(value / 1e6).toFixed(1)}M`
  if (Math.abs(value) >= 1e3) return `${(value / 1e3).toFixed(1)}K`
  return value.toFixed(value < 1 ? 4 : 2)
}

/** Format token price — handles micro-prices like 0.00000002345 */
export function formatPrice(value: number | null | undefined): string {
  if (value == null) return "—"
  if (value === 0) return "$0"
  const abs = Math.abs(value)
  if (abs >= 1000) return `$${formatCompact(value)}`
  if (abs >= 1) return `$${value.toFixed(2)}`
  if (abs >= 0.01) return `$${value.toFixed(4)}`
  // Micro-prices: count leading zeros, show 2 significant digits
  // e.g. 0.00000002345 → $0.0₇23
  const str = abs.toExponential()
  const match = str.match(/^(\d+\.\d+)e([+-]\d+)$/)
  if (match) {
    const exp = parseInt(match[2], 10)
    if (exp < -2) {
      const zeros = Math.abs(exp) - 1
      const sigFigs = abs * Math.pow(10, Math.abs(exp))
      const digits = sigFigs.toFixed(0).padStart(2, "0").slice(0, 2)
      const sub = String(zeros).split("").map(d => "₀₁₂₃₄₅₆₇₈₉"[parseInt(d)]).join("")
      return `$0.0${sub}${digits}`
    }
  }
  return `$${value.toFixed(4)}`
}

/** Format USD */
export function formatUsd(value: number | null | undefined): string {
  if (value == null) return "—"
  return `$${formatCompact(value)}`
}

/** Format percentage */
export function formatPct(value: number | null | undefined): string {
  if (value == null) return "—"
  const sign = value >= 0 ? "+" : ""
  return `${sign}${value.toFixed(1)}%`
}

/** Format SOL */
export function formatSol(value: number | null | undefined): string {
  if (value == null) return "—"
  return `${value.toFixed(2)} SOL`
}

/** Format seconds to human-readable duration */
export function formatDuration(seconds: number): string {
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

/** Truncate address: AbCd...xYzW */
export function truncateAddress(address: string, chars = 4): string {
  if (address.length <= chars * 2 + 3) return address
  return `${address.slice(0, chars)}...${address.slice(-chars)}`
}

/** Relative time: "2m ago", "3h ago" */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—"
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60) return "just now"
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}
