import { cn } from "@/lib/utils"

const signalStyles: Record<string, string> = {
  strong_buy: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  buy: "bg-green-500/20 text-green-400 border-green-500/30",
  watch: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  avoid: "bg-red-500/20 text-red-400 border-red-500/30",
  expired: "bg-neutral-500/20 text-neutral-400 border-neutral-500/30",
}

export function SignalBadge({
  status,
  className,
}: {
  status: string
  className?: string
}) {
  const label = status.replace(/_/g, " ").toUpperCase()
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider",
        signalStyles[status] ?? "bg-neutral-500/20 text-neutral-400",
        className,
      )}
    >
      {label}
    </span>
  )
}
