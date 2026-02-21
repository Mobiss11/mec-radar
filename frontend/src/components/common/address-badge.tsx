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
        "inline-flex items-center rounded bg-muted/50 px-1.5 py-0.5 text-xs font-data text-muted-foreground",
        "cursor-pointer hover:bg-muted hover:text-foreground transition-colors",
        className,
      )}
      title={address}
      onClick={() => navigator.clipboard.writeText(address)}
    >
      {truncateAddress(address)}
    </span>
  )
}
