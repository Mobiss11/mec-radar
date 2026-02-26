import { useCallback, useState } from "react"
import { usePolling } from "@/hooks/use-polling"
import { useAuth } from "@/hooks/use-auth"
import { copyTrading } from "@/lib/api"
import { Pagination } from "@/components/common/pagination"
import { StatCard } from "@/components/common/stat-card"
import { EmptyState } from "@/components/common/empty-state"
import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import { formatPct, formatSol, formatUsd, formatPrice, formatDateMsk, timeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"
import {
  Users,
  TrendingUp,
  TrendingDown,
  Target,
  Wallet,
  Plus,
  Trash2,
  ToggleLeft,
  ToggleRight,
  ExternalLink,
  Copy,
  X,
  Loader2,
} from "lucide-react"

/* ---------- Add Wallet Dialog ---------- */
function AddWalletForm({
  onAdd,
  onCancel,
}: {
  onAdd: (data: { address: string; label: string; multiplier: number; max_sol_per_trade: number }) => Promise<void>
  onCancel: () => void
}) {
  const [address, setAddress] = useState("")
  const [label, setLabel] = useState("")
  const [multiplier, setMultiplier] = useState("1.0")
  const [maxSol, setMaxSol] = useState("0.05")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  const handleSubmit = async () => {
    if (!address.trim()) {
      setError("Enter wallet address")
      return
    }
    if (address.trim().length < 32 || address.trim().length > 44) {
      setError("Invalid Solana address (32-44 chars)")
      return
    }
    setSubmitting(true)
    setError("")
    try {
      await onAdd({
        address: address.trim(),
        label: label.trim(),
        multiplier: parseFloat(multiplier) || 1.0,
        max_sol_per_trade: parseFloat(maxSol) || 0.05,
      })
      setAddress("")
      setLabel("")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add wallet")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="rounded-xl border border-primary/30 bg-card/80 p-4 backdrop-blur-sm">
      <div className="mb-3 flex items-center justify-between">
        <h4 className="text-sm font-semibold">Add Tracked Wallet</h4>
        <Button variant="ghost" size="icon" className="h-6 w-6" onClick={onCancel}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      <div className="space-y-3">
        <div>
          <label className="mb-1 block text-xs text-muted-foreground">Wallet Address *</label>
          <input
            type="text"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="7xK...abc"
            className="w-full rounded-lg border border-border/50 bg-background/50 px-3 py-2 text-sm font-mono placeholder:text-muted-foreground/40 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/20"
          />
        </div>

        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Label</label>
            <input
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Whale #1"
              className="w-full rounded-lg border border-border/50 bg-background/50 px-3 py-2 text-sm placeholder:text-muted-foreground/40 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/20"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Multiplier</label>
            <input
              type="number"
              step="0.1"
              min="0.01"
              max="100"
              value={multiplier}
              onChange={(e) => setMultiplier(e.target.value)}
              className="w-full rounded-lg border border-border/50 bg-background/50 px-3 py-2 text-sm font-data focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/20"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Max SOL/trade</label>
            <input
              type="number"
              step="0.01"
              min="0.001"
              max="10"
              value={maxSol}
              onChange={(e) => setMaxSol(e.target.value)}
              className="w-full rounded-lg border border-border/50 bg-background/50 px-3 py-2 text-sm font-data focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/20"
            />
          </div>
        </div>

        {error && (
          <p className="text-xs text-red-400">{error}</p>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onCancel} className="text-xs">
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={handleSubmit}
            disabled={submitting}
            className="text-xs"
          >
            {submitting ? <Loader2 className="mr-1.5 h-3 w-3 animate-spin" /> : <Plus className="mr-1.5 h-3 w-3" />}
            Add Wallet
          </Button>
        </div>
      </div>
    </div>
  )
}

/* ---------- Wallet Card ---------- */
function WalletCard({
  wallet,
  onToggle,
  onRemove,
}: {
  wallet: {
    address: string
    label: string
    multiplier: number
    max_sol_per_trade: number
    enabled: boolean
    added_at: string
  }
  onToggle: () => void
  onRemove: () => void
}) {
  const [confirmDelete, setConfirmDelete] = useState(false)

  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-xl border border-border/50 bg-card/60 p-3 backdrop-blur-sm transition-all",
        !wallet.enabled && "opacity-50",
      )}
    >
      {/* Toggle */}
      <button
        onClick={onToggle}
        className="flex-shrink-0 text-muted-foreground hover:text-primary transition-colors"
        title={wallet.enabled ? "Disable tracking" : "Enable tracking"}
      >
        {wallet.enabled ? (
          <ToggleRight className="h-5 w-5 text-emerald-400" />
        ) : (
          <ToggleLeft className="h-5 w-5" />
        )}
      </button>

      {/* Info */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">
            {wallet.label || "Untitled"}
          </span>
          <span
            className="inline-flex items-center gap-1 rounded bg-muted/50 px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground cursor-pointer hover:bg-muted transition-colors"
            title={`Copy: ${wallet.address}`}
            onClick={() => navigator.clipboard.writeText(wallet.address)}
          >
            {wallet.address.slice(0, 4)}...{wallet.address.slice(-4)}
            <Copy className="h-2.5 w-2.5" />
          </span>
          <a
            href={`https://gmgn.ai/sol/address/${wallet.address}`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-0.5 text-[10px] text-primary/60 hover:text-primary transition-colors"
          >
            gmgn
            <ExternalLink className="h-2.5 w-2.5" />
          </a>
        </div>
        <div className="mt-0.5 flex gap-3 text-xs text-muted-foreground font-data">
          <span>×{wallet.multiplier}</span>
          <span>Max: {wallet.max_sol_per_trade} SOL</span>
          <span>{timeAgo(wallet.added_at)}</span>
        </div>
      </div>

      {/* Delete */}
      {confirmDelete ? (
        <div className="flex items-center gap-1">
          <Button
            variant="destructive"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => {
              onRemove()
              setConfirmDelete(false)
            }}
          >
            Confirm
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-1.5"
            onClick={() => setConfirmDelete(false)}
          >
            <X className="h-3 w-3" />
          </Button>
        </div>
      ) : (
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-muted-foreground hover:text-red-400 hover:bg-red-500/10"
          onClick={() => setConfirmDelete(true)}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      )}
    </div>
  )
}

/* ---------- Main Page ---------- */
export function CopyTradingPage() {
  const [showAddForm, setShowAddForm] = useState(false)
  const [posStatus, setPosStatus] = useState("all")
  const [page, setPage] = useState(1)
  const [refreshKey, setRefreshKey] = useState(0)
  const { refreshCsrf } = useAuth()

  /* Polling: summary */
  const { data: summary, loading: sumLoading } = usePolling({
    fetcher: () => copyTrading.summary(),
    interval: 15000,
    key: `ct-summary-${refreshKey}`,
  })

  /* Polling: wallets */
  const { data: walletsData, loading: walletsLoading } = usePolling({
    fetcher: () => copyTrading.wallets(),
    interval: 15000,
    key: `ct-wallets-${refreshKey}`,
  })

  /* Polling: positions */
  const posFetcher = useCallback(
    () =>
      copyTrading.positions({
        status: posStatus,
        page,
        limit: 20,
      }),
    [posStatus, page],
  )

  const { data: posData, loading: posLoading } = usePolling({
    fetcher: posFetcher,
    interval: 15000,
    key: `ct-pos-${posStatus}-${page}-${refreshKey}`,
  })

  const s = summary as Record<string, number | boolean> | null
  const wallets = (walletsData?.items ?? []) as Array<{
    address: string
    label: string
    multiplier: number
    max_sol_per_trade: number
    enabled: boolean
    added_at: string
  }>
  const positions = (posData?.items ?? []) as Array<Record<string, unknown>>

  /* Handlers */
  const handleAddWallet = async (data: {
    address: string
    label: string
    multiplier: number
    max_sol_per_trade: number
  }) => {
    const token = await refreshCsrf()
    await copyTrading.addWallet(data, token)
    setShowAddForm(false)
    setRefreshKey((k) => k + 1)
  }

  const handleToggleWallet = async (address: string, currentEnabled: boolean) => {
    const token = await refreshCsrf()
    await copyTrading.updateWallet(address, { enabled: !currentEnabled }, token)
    setRefreshKey((k) => k + 1)
  }

  const handleRemoveWallet = async (address: string) => {
    const token = await refreshCsrf()
    await copyTrading.removeWallet(address, token)
    setRefreshKey((k) => k + 1)
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Copy Trading</h1>
        <p className="text-sm text-muted-foreground">
          Track whale wallets and mirror their trades automatically
        </p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard
          label="Active Wallets"
          value={s ? `${s.active_wallets}/${s.total_wallets}` : null}
          icon={<Users className="h-4 w-4" />}
          loading={sumLoading}
        />
        <StatCard
          label="Open Positions"
          value={s ? String(s.open_positions) : null}
          trend={s ? `${formatSol(s.total_invested_sol as number)} invested` : undefined}
          icon={<Wallet className="h-4 w-4" />}
          loading={sumLoading}
        />
        <StatCard
          label="Total PnL"
          value={s ? formatUsd(s.total_pnl_usd as number) : null}
          icon={
            s && (s.total_pnl_usd as number) >= 0 ? (
              <TrendingUp className="h-4 w-4" />
            ) : (
              <TrendingDown className="h-4 w-4" />
            )
          }
          loading={sumLoading}
        />
        <StatCard
          label="Win Rate"
          value={s ? `${s.win_rate}%` : null}
          trend={s ? `${s.wins}W / ${s.losses}L` : undefined}
          icon={<Target className="h-4 w-4" />}
          loading={sumLoading}
        />
        <StatCard
          label="Closed"
          value={s ? String(s.closed_count) : null}
          loading={sumLoading}
        />
      </div>

      {/* Tracked Wallets Section */}
      <div>
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-base font-semibold">Tracked Wallets</h3>
          {!showAddForm && (
            <Button
              size="sm"
              variant="outline"
              className="text-xs"
              onClick={() => setShowAddForm(true)}
            >
              <Plus className="mr-1.5 h-3 w-3" />
              Add Wallet
            </Button>
          )}
        </div>

        {showAddForm && (
          <div className="mb-3">
            <AddWalletForm
              onAdd={handleAddWallet}
              onCancel={() => setShowAddForm(false)}
            />
          </div>
        )}

        {walletsLoading && !walletsData ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-16 rounded-lg" />
            ))}
          </div>
        ) : wallets.length === 0 ? (
          <EmptyState
            icon={<Users className="h-10 w-10" />}
            title="No tracked wallets"
            description="Add a whale wallet to start copy trading"
          />
        ) : (
          <div className="space-y-2">
            {wallets.map((w) => (
              <WalletCard
                key={w.address}
                wallet={w}
                onToggle={() => handleToggleWallet(w.address, w.enabled)}
                onRemove={() => handleRemoveWallet(w.address)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Positions Section */}
      <div>
        <div className="mb-3 flex items-center gap-2">
          <h3 className="text-base font-semibold">Copy Trade Positions</h3>
          <div className="flex gap-1">
            {["all", "open", "closed"].map((st) => (
              <Button
                key={st}
                variant={posStatus === st ? "secondary" : "ghost"}
                size="sm"
                className="text-xs capitalize"
                onClick={() => {
                  setPosStatus(st)
                  setPage(1)
                }}
              >
                {st}
              </Button>
            ))}
          </div>
        </div>

        {posLoading && !posData ? (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-16 rounded-lg" />
            ))}
          </div>
        ) : positions.length === 0 ? (
          <EmptyState
            icon={<TrendingUp className="h-10 w-10" />}
            title={`No ${posStatus === "all" ? "" : posStatus + " "}copy trade positions`}
            description="Positions will appear when tracked wallets make trades"
          />
        ) : (
          <div className="space-y-2">
            {positions.map((p) => (
              <div
                key={p.id as number}
                className="flex items-center gap-4 rounded-xl border border-border/50 bg-card/60 p-4 backdrop-blur-sm"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">
                      {(p.symbol as string) ?? "???"}
                    </span>
                    <span
                      className="inline-flex items-center rounded bg-muted/50 px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground cursor-pointer hover:bg-muted transition-colors"
                      title={`Copy: ${p.token_address}`}
                      onClick={() =>
                        navigator.clipboard.writeText(p.token_address as string)
                      }
                    >
                      {(p.token_address as string).slice(0, 4)}...
                      {(p.token_address as string).slice(-4)}
                    </span>
                    <span className="inline-flex items-center rounded-full bg-cyan-500/15 px-2 py-0.5 text-[10px] font-semibold text-cyan-400">
                      copy
                    </span>
                    {p.is_paper === false && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-400">
                        <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                        REAL
                      </span>
                    )}
                    {p.status === "closed" && p.close_reason && (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        {String(p.close_reason)}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 flex flex-wrap gap-3 text-xs text-muted-foreground font-data">
                    <span>{formatSol(p.amount_sol_invested as number)}</span>
                    <span>
                      Entry: {formatPrice(p.entry_price as number)}
                    </span>
                    {p.current_price != null && (
                      <span>
                        Now: {formatPrice(p.current_price as number)}
                      </span>
                    )}
                    <span title={p.opened_at ? String(p.opened_at) : ""}>
                      {formatDateMsk(p.opened_at as string)}
                    </span>
                    {p.closed_at && (
                      <span>→ {formatDateMsk(p.closed_at as string)}</span>
                    )}
                    <span className="text-muted-foreground/50">
                      ({timeAgo((p.closed_at ?? p.opened_at) as string)})
                    </span>
                    <a
                      href={`https://gmgn.ai/sol/token/${p.token_address}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-0.5 text-primary/70 hover:text-primary transition-colors"
                    >
                      gmgn
                      <ExternalLink className="h-3 w-3" />
                    </a>
                    {typeof p.tx_hash === "string" && p.tx_hash && (
                      <a
                        href={`https://solscan.io/tx/${p.tx_hash}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-0.5 text-primary/70 hover:text-primary transition-colors"
                      >
                        tx
                        <ExternalLink className="h-3 w-3" />
                      </a>
                    )}
                  </div>
                </div>
                <div className="text-right">
                  <p
                    className={cn(
                      "font-data text-sm font-bold",
                      (p.pnl_pct as number) >= 0
                        ? "text-emerald-400"
                        : "text-red-400",
                    )}
                  >
                    {formatPct(p.pnl_pct as number)}
                  </p>
                  {p.pnl_usd != null && (
                    <p className="font-data text-xs text-muted-foreground">
                      {formatUsd(p.pnl_usd as number)}
                    </p>
                  )}
                </div>
              </div>
            ))}

            {posData?.total_pages != null &&
              (posData.total_pages as number) > 1 && (
                <Pagination
                  page={(posData.page as number) ?? page}
                  totalPages={posData.total_pages as number}
                  onPageChange={setPage}
                  className="pt-3"
                />
              )}
          </div>
        )}
      </div>
    </div>
  )
}
