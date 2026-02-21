import { useCallback, useState } from "react"
import { usePolling } from "@/hooks/use-polling"
import { signals } from "@/lib/api"
import { SignalBadge } from "@/components/common/signal-badge"
import { ScoreBadge } from "@/components/common/score-badge"
import { AddressBadge } from "@/components/common/address-badge"
import { EmptyState } from "@/components/common/empty-state"
import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import { timeAgo } from "@/lib/format"
import { Signal, ChevronRight } from "lucide-react"

const statusTabs = [
  { label: "All Active", value: "strong_buy,buy,watch" },
  { label: "Strong Buy", value: "strong_buy" },
  { label: "Buy", value: "buy" },
  { label: "Watch", value: "watch" },
  { label: "Expired", value: "expired" },
]

export function SignalsPage() {
  const [statusFilter, setStatusFilter] = useState("strong_buy,buy,watch")
  const [cursor, setCursor] = useState<number | undefined>(undefined)

  const fetcher = useCallback(
    () =>
      signals.list({
        status: statusFilter,
        limit: 20,
        ...(cursor != null ? { cursor } : {}),
      }),
    [statusFilter, cursor],
  )

  const { data, loading } = usePolling({ fetcher, interval: 10000, key: `${statusFilter}-${cursor}` })
  const items = (data?.items ?? []) as Array<Record<string, unknown>>

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Signals</h1>
        <p className="text-sm text-muted-foreground">
          Trading signals from the 54-rule engine
        </p>
      </div>

      {/* Status tabs */}
      <div className="flex gap-1">
        {statusTabs.map((tab) => (
          <Button
            key={tab.value}
            variant={statusFilter === tab.value ? "secondary" : "ghost"}
            size="sm"
            onClick={() => {
              setStatusFilter(tab.value)
              setCursor(undefined)
            }}
            className="text-xs"
          >
            {tab.label}
          </Button>
        ))}
      </div>

      {/* List */}
      {loading && !data ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-16 rounded-lg" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={<Signal className="h-12 w-12" />}
          title="No signals"
          description="No signals match this filter"
        />
      ) : (
        <div className="space-y-2">
          {items.map((s) => (
            <div
              key={s.id as number}
              className="flex items-center gap-4 rounded-xl border border-border/50 bg-card/60 p-4 backdrop-blur-sm hover:border-border transition-colors"
            >
              {/* Token icon */}
              {s.token_image_url ? (
                <img
                  src={s.token_image_url as string}
                  alt=""
                  className="h-8 w-8 rounded-full"
                />
              ) : (
                <div className="flex h-8 w-8 items-center justify-center rounded-full bg-muted text-xs font-bold">
                  {(s.token_symbol as string)?.[0] ?? "?"}
                </div>
              )}

              {/* Info */}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium">
                    {(s.token_symbol as string) ?? "???"}
                  </span>
                  <SignalBadge status={s.status as string} />
                  <ScoreBadge score={s.score as number} />
                </div>
                <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                  <AddressBadge address={s.token_address as string} />
                  <span>{timeAgo(s.updated_at as string)}</span>
                </div>
              </div>
            </div>
          ))}

          {/* Load more */}
          {data?.has_more && items.length > 0 && (
            <div className="flex justify-center pt-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  setCursor(items[items.length - 1]?.id as number)
                }
              >
                Load more
                <ChevronRight className="h-4 w-4 ml-1" />
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
