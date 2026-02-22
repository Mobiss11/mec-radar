import { ExternalLink } from "lucide-react"

import { cn } from "@/lib/utils"
import { truncateAddress } from "@/lib/format"

export function AddressBadge({
  address,
  className,
}: {
  address: string
  className?: string
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-xs font-data text-muted-foreground",
        className,
      )}
    >
      <span
        className="inline-flex items-center rounded bg-muted/50 px-1.5 py-0.5 cursor-pointer hover:bg-muted hover:text-foreground transition-colors"
        title={`Copy: ${address}`}
        onClick={() => navigator.clipboard.writeText(address)}
      >
        {truncateAddress(address)}
      </span>
      <a
        href={`https://gmgn.ai/sol/token/${address}`}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-0.5 text-primary/60 hover:text-primary transition-colors"
        title="Open on GMGN"
        onClick={(e) => e.stopPropagation()}
      >
        gmgn
        <ExternalLink className="h-2.5 w-2.5" />
      </a>
    </span>
  )
}
