import { cn } from "@/lib/utils"
import { Skeleton } from "@/components/ui/skeleton"
import type { ReactNode } from "react"

interface StatCardProps {
  label: string
  value: string | number | null
  icon?: ReactNode
  trend?: string
  trendUp?: boolean
  loading?: boolean
  className?: string
}

export function StatCard({
  label,
  value,
  icon,
  trend,
  trendUp,
  loading,
  className,
}: StatCardProps) {
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-xl border border-border/50",
        "bg-card/60 backdrop-blur-sm p-5",
        "transition-colors hover:border-border",
        className,
      )}
    >
      <div className="flex items-start justify-between">
        <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          {label}
        </p>
        {icon && (
          <span className="text-muted-foreground/50">{icon}</span>
        )}
      </div>
      {loading ? (
        <Skeleton className="mt-2 h-8 w-24" />
      ) : (
        <p className="mt-2 font-data text-2xl font-bold tracking-tight">
          {value ?? "—"}
        </p>
      )}
      {trend && !loading && (
        <p
          className={cn(
            "mt-1 text-xs font-medium font-data",
            trendUp ? "text-emerald-400" : "text-red-400",
          )}
        >
          {trend}
        </p>
      )}
    </div>
  )
}
