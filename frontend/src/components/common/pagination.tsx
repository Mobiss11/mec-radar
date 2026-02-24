import { cn } from "@/lib/utils"
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react"

interface PaginationProps {
  page: number
  totalPages: number
  onPageChange: (page: number) => void
  className?: string
}

/** Generate page numbers to show: always show first, last, current ± siblings, with ellipses */
function getPageNumbers(current: number, total: number): (number | "...")[] {
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1)
  }

  const pages: (number | "...")[] = []
  const siblings = 1

  // Always first page
  pages.push(1)

  const rangeStart = Math.max(2, current - siblings)
  const rangeEnd = Math.min(total - 1, current + siblings)

  if (rangeStart > 2) pages.push("...")

  for (let i = rangeStart; i <= rangeEnd; i++) {
    pages.push(i)
  }

  if (rangeEnd < total - 1) pages.push("...")

  // Always last page
  if (total > 1) pages.push(total)

  return pages
}

export function Pagination({ page, totalPages, onPageChange, className }: PaginationProps) {
  if (totalPages <= 1) return null

  const pages = getPageNumbers(page, totalPages)

  return (
    <nav
      className={cn("flex items-center justify-center gap-1", className)}
      aria-label="Pagination"
    >
      {/* First page */}
      <button
        onClick={() => onPageChange(1)}
        disabled={page === 1}
        className={cn(
          "flex h-8 w-8 items-center justify-center rounded-md text-xs transition-colors",
          page === 1
            ? "text-muted-foreground/30 cursor-not-allowed"
            : "text-muted-foreground hover:text-foreground hover:bg-card/80",
        )}
        aria-label="First page"
      >
        <ChevronsLeft className="h-3.5 w-3.5" />
      </button>

      {/* Previous */}
      <button
        onClick={() => onPageChange(page - 1)}
        disabled={page === 1}
        className={cn(
          "flex h-8 w-8 items-center justify-center rounded-md text-xs transition-colors",
          page === 1
            ? "text-muted-foreground/30 cursor-not-allowed"
            : "text-muted-foreground hover:text-foreground hover:bg-card/80",
        )}
        aria-label="Previous page"
      >
        <ChevronLeft className="h-3.5 w-3.5" />
      </button>

      {/* Page numbers */}
      {pages.map((p, idx) =>
        p === "..." ? (
          <span
            key={`ellipsis-${idx}`}
            className="flex h-8 w-8 items-center justify-center text-xs text-muted-foreground/50"
          >
            ...
          </span>
        ) : (
          <button
            key={p}
            onClick={() => onPageChange(p)}
            className={cn(
              "flex h-8 min-w-8 items-center justify-center rounded-md px-2 text-xs font-medium transition-colors",
              page === p
                ? "bg-primary/15 text-primary shadow-sm"
                : "text-muted-foreground hover:text-foreground hover:bg-card/80",
            )}
            aria-label={`Page ${p}`}
            aria-current={page === p ? "page" : undefined}
          >
            {p}
          </button>
        ),
      )}

      {/* Next */}
      <button
        onClick={() => onPageChange(page + 1)}
        disabled={page === totalPages}
        className={cn(
          "flex h-8 w-8 items-center justify-center rounded-md text-xs transition-colors",
          page === totalPages
            ? "text-muted-foreground/30 cursor-not-allowed"
            : "text-muted-foreground hover:text-foreground hover:bg-card/80",
        )}
        aria-label="Next page"
      >
        <ChevronRight className="h-3.5 w-3.5" />
      </button>

      {/* Last page */}
      <button
        onClick={() => onPageChange(totalPages)}
        disabled={page === totalPages}
        className={cn(
          "flex h-8 w-8 items-center justify-center rounded-md text-xs transition-colors",
          page === totalPages
            ? "text-muted-foreground/30 cursor-not-allowed"
            : "text-muted-foreground hover:text-foreground hover:bg-card/80",
        )}
        aria-label="Last page"
      >
        <ChevronsRight className="h-3.5 w-3.5" />
      </button>
    </nav>
  )
}
