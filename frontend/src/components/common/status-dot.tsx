import { cn } from "@/lib/utils"

const colorMap = {
  connected: "bg-emerald-400",
  disconnected: "bg-red-400",
  degraded: "bg-amber-400",
  disabled: "bg-neutral-500",
} as const

type Status = keyof typeof colorMap

export function StatusDot({
  status,
  className,
}: {
  status: Status
  className?: string
}) {
  return (
    <span className={cn("relative flex h-2.5 w-2.5", className)}>
      {status === "connected" && (
        <span
          className={cn(
            "absolute inline-flex h-full w-full rounded-full opacity-75",
            colorMap[status],
            "animate-pulse-dot",
          )}
        />
      )}
      <span
        className={cn(
          "relative inline-flex h-2.5 w-2.5 rounded-full",
          colorMap[status],
        )}
      />
    </span>
  )
}
