import { useCallback, useState } from "react"
import { usePolling } from "@/hooks/use-polling"
import { tokens } from "@/lib/api"
import { Input } from "@/components/ui/input"
import { ScoreBadge } from "@/components/common/score-badge"
import { AddressBadge } from "@/components/common/address-badge"
import { EmptyState } from "@/components/common/empty-state"
import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { formatCompact, formatUsd, timeAgo } from "@/lib/format"
import { Coins, Search, ChevronLeft, ChevronRight, Filter } from "lucide-react"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"

const SORT_OPTIONS = [
  { value: "score", label: "Score (high→low)" },
  { value: "newest", label: "Newest first" },
  { value: "mcap", label: "Market Cap" },
  { value: "liquidity", label: "Liquidity" },
] as const

export function TokensPage() {
  const [search, setSearch] = useState("")
  const [sort, setSort] = useState("score")
  const [enrichedOnly, setEnrichedOnly] = useState(true)
  const [cursor, setCursor] = useState<number | undefined>(undefined)
  const [selectedAddress, setSelectedAddress] = useState<string | null>(null)

  const fetcher = useCallback(
    () =>
      tokens.list({
        limit: 20,
        search,
        sort,
        enriched_only: enrichedOnly ? "true" : "",
        ...(cursor != null ? { cursor } : {}),
      }),
    [search, sort, enrichedOnly, cursor],
  )

  const { data, loading } = usePolling({ fetcher, interval: 10000 })

  const { data: detail, loading: detailLoading } = usePolling({
    fetcher: () => tokens.detail(selectedAddress!),
    interval: 15000,
    enabled: !!selectedAddress,
  })

  const items = (data?.items ?? []) as Array<Record<string, unknown>>

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Tokens</h1>
          <p className="text-sm text-muted-foreground">
            Discovered tokens with scoring data
          </p>
        </div>
      </div>

      {/* Search + Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[200px] max-w-sm">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground/50" />
          <Input
            placeholder="Search by name, symbol, or address..."
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setCursor(undefined)
            }}
            className="pl-10"
          />
        </div>
        <Select value={sort} onValueChange={(v) => { setSort(v); setCursor(undefined) }}>
          <SelectTrigger className="w-[160px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SORT_OPTIONS.map((o) => (
              <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant={enrichedOnly ? "default" : "outline"}
          size="sm"
          onClick={() => { setEnrichedOnly(!enrichedOnly); setCursor(undefined) }}
          className="gap-1.5"
        >
          <Filter className="h-3.5 w-3.5" />
          {enrichedOnly ? "Enriched" : "All"}
        </Button>
      </div>

      {/* Table */}
      {loading && !data ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-12 rounded-lg" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={<Coins className="h-12 w-12" />}
          title="No tokens found"
          description="Try adjusting your search"
        />
      ) : (
        <>
          <div className="rounded-xl border border-border/50 bg-card/60 backdrop-blur-sm overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/30 text-xs text-muted-foreground">
                  <th className="px-4 py-3 text-left font-medium">Token</th>
                  <th className="px-4 py-3 text-right font-medium">Score</th>
                  <th className="px-4 py-3 text-right font-medium hidden md:table-cell">Price</th>
                  <th className="px-4 py-3 text-right font-medium hidden md:table-cell">MCap</th>
                  <th className="px-4 py-3 text-right font-medium hidden lg:table-cell">Liquidity</th>
                  <th className="px-4 py-3 text-right font-medium hidden lg:table-cell">Holders</th>
                  <th className="px-4 py-3 text-right font-medium">Age</th>
                </tr>
              </thead>
              <tbody>
                {items.map((t) => (
                  <tr
                    key={t.id as number}
                    className="border-b border-border/20 hover:bg-accent/30 cursor-pointer transition-colors"
                    onClick={() => setSelectedAddress(t.address as string)}
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        {t.image_url ? (
                          <img
                            src={t.image_url as string}
                            alt=""
                            className="h-6 w-6 rounded-full"
                          />
                        ) : (
                          <div className="flex h-6 w-6 items-center justify-center rounded-full bg-muted text-[10px] font-bold">
                            {(t.symbol as string)?.[0] ?? "?"}
                          </div>
                        )}
                        <div className="min-w-0">
                          <p className="font-medium truncate">
                            {(t.symbol as string) ?? "???"}
                          </p>
                          <AddressBadge address={t.address as string} />
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex justify-end gap-1">
                        <ScoreBadge score={t.score as number} />
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right font-data hidden md:table-cell">
                      {formatUsd(t.price as number)}
                    </td>
                    <td className="px-4 py-3 text-right font-data hidden md:table-cell">
                      {formatUsd(t.market_cap as number)}
                    </td>
                    <td className="px-4 py-3 text-right font-data hidden lg:table-cell">
                      {formatUsd(t.liquidity_usd as number)}
                    </td>
                    <td className="px-4 py-3 text-right font-data hidden lg:table-cell">
                      {formatCompact(t.holders_count as number)}
                    </td>
                    <td className="px-4 py-3 text-right text-xs text-muted-foreground">
                      {timeAgo(t.created_at as string)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex justify-end gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={cursor == null}
              onClick={() => setCursor(undefined)}
            >
              <ChevronLeft className="h-4 w-4 mr-1" />
              First
            </Button>
            {data?.has_more && items.length > 0 && (
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  setCursor(items[items.length - 1]?.id as number)
                }
              >
                Next
                <ChevronRight className="h-4 w-4 ml-1" />
              </Button>
            )}
          </div>
        </>
      )}

      {/* Detail Sheet */}
      <Sheet
        open={!!selectedAddress}
        onOpenChange={(open) => !open && setSelectedAddress(null)}
      >
        <SheetContent className="overflow-y-auto sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>Token Detail</SheetTitle>
          </SheetHeader>
          {detailLoading ? (
            <div className="space-y-3 mt-4">
              <Skeleton className="h-6 w-48" />
              <Skeleton className="h-32 w-full" />
              <Skeleton className="h-32 w-full" />
            </div>
          ) : detail ? (
            <div className="mt-4 space-y-4">
              <TokenDetailView data={detail as Record<string, unknown>} />
            </div>
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  )
}

function TokenDetailView({ data }: { data: Record<string, unknown> }) {
  const token = data.token as Record<string, unknown> | null
  const snap = data.latest_snapshot as Record<string, unknown> | null
  const security = data.security as Record<string, unknown> | null
  const signal = data.active_signal as Record<string, unknown> | null

  if (!token) return <p className="text-muted-foreground">No data</p>

  return (
    <>
      <div>
        <h3 className="font-semibold">
          {(token.symbol as string) ?? "Unknown"}{" "}
          <span className="text-muted-foreground font-normal text-sm">
            {token.name as string}
          </span>
        </h3>
        <AddressBadge address={token.address as string} className="mt-1" />
      </div>

      {snap && (
        <Section title="Latest Snapshot">
          <Row label="Score v2" value={String(snap.score ?? "—")} />
          <Row label="Score v3" value={String(snap.score_v3 ?? "—")} />
          <Row label="Price" value={formatUsd(snap.price as number)} />
          <Row label="Market Cap" value={formatUsd(snap.market_cap as number)} />
          <Row label="Liquidity" value={formatUsd(snap.liquidity_usd as number)} />
          <Row label="Volume (1h)" value={formatUsd(snap.volume_1h as number)} />
          <Row label="Holders" value={formatCompact(snap.holders_count as number)} />
          <Row label="Stage" value={snap.stage as string} />
        </Section>
      )}

      {security && (
        <Section title="Security">
          <Row label="Is Mintable" value={String(security.is_mintable ?? "—")} />
          <Row label="Has Freeze" value={String(security.has_freeze_authority ?? "—")} />
          <Row label="Is Honeypot" value={String(security.is_honeypot ?? "—")} />
          <Row label="RugCheck" value={String(security.rugcheck_risk ?? "—")} />
        </Section>
      )}

      {signal && (
        <Section title="Active Signal">
          <Row label="Status" value={signal.status as string} />
          <Row label="Score" value={String(signal.score)} />
        </Section>
      )}
    </>
  )
}

function Section({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-lg border border-border/30 bg-muted/20 p-3">
      <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
        {title}
      </p>
      <div className="space-y-1">{children}</div>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-data">{value}</span>
    </div>
  )
}
