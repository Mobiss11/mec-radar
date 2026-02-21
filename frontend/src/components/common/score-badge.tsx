import { cn } from "@/lib/utils"

function getScoreColor(score: number | null | undefined): string {
  if (score == null) return "bg-neutral-700 text-neutral-400"
  if (score >= 70) return "bg-emerald-500/20 text-emerald-400"
  if (score >= 50) return "bg-amber-500/20 text-amber-400"
  if (score >= 30) return "bg-orange-500/20 text-orange-400"
  return "bg-red-500/20 text-red-400"
}

export function ScoreBadge({
  score,
  className,
}: {
  score: number | null | undefined
  className?: string
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md px-2 py-0.5 text-xs font-bold font-data",
        getScoreColor(score),
        className,
      )}
    >
      {score ?? "—"}
    </span>
  )
}
