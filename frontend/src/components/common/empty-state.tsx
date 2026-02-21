import { cn } from "@/lib/utils"
import type { ReactNode } from "react"

export function EmptyState({
  icon,
  title,
  description,
  className,
}: {
  icon?: ReactNode
  title: string
  description?: string
  className?: string
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center py-16 text-center",
        className,
      )}
    >
      {icon && (
        <div className="mb-4 text-muted-foreground/30">{icon}</div>
      )}
      <h3 className="text-base font-semibold text-muted-foreground">
        {title}
      </h3>
      {description && (
        <p className="mt-1 text-sm text-muted-foreground/70">
          {description}
        </p>
      )}
    </div>
  )
}
