/** Format number with K/M/B suffixes */
export function formatCompact(value: number | null | undefined): string {
  if (value == null) return "—"
  if (Math.abs(value) >= 1e9) return `${(value / 1e9).toFixed(1)}B`
  if (Math.abs(value) >= 1e6) return `${(value / 1e6).toFixed(1)}M`
  if (Math.abs(value) >= 1e3) return `${(value / 1e3).toFixed(1)}K`
  return value.toFixed(value < 1 ? 4 : 2)
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
