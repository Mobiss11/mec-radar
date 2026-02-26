import { NavLink, Outlet } from "react-router-dom"
import { cn } from "@/lib/utils"
import { useAuth } from "@/hooks/use-auth"
import {
  LayoutDashboard,
  Coins,
  Signal,
  Briefcase,
  Users,
  Settings,
  BarChart3,
  LogOut,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"

const navItems = [
  { to: "/", icon: LayoutDashboard, label: "Overview" },
  { to: "/tokens", icon: Coins, label: "Tokens" },
  { to: "/signals", icon: Signal, label: "Signals" },
  { to: "/portfolio", icon: Briefcase, label: "Portfolio" },
  { to: "/copy-trading", icon: Users, label: "Copy Trading" },
  { to: "/analytics", icon: BarChart3, label: "Analytics" },
  { to: "/settings", icon: Settings, label: "Settings" },
]

export function DashboardLayout() {
  const { username, logout } = useAuth()

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="flex w-56 flex-col border-r border-border/50 bg-card/30 backdrop-blur-sm">
        {/* Logo */}
        <div className="flex items-center gap-2 px-5 py-5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/20">
            <Coins className="h-4 w-4 text-primary" />
          </div>
          <span className="text-sm font-bold tracking-tight">
            Memecoin
            <span className="text-primary"> Radar</span>
          </span>
        </div>

        <Separator className="opacity-30" />

        {/* Nav */}
        <nav className="flex-1 space-y-0.5 px-3 py-4">
          {navItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                  isActive
                    ? "bg-primary/10 text-primary font-medium"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          ))}
        </nav>

        <Separator className="opacity-30" />

        {/* User */}
        <div className="flex items-center justify-between px-4 py-3">
          <span className="text-xs text-muted-foreground">{username}</span>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-muted-foreground hover:text-destructive"
            onClick={logout}
          >
            <LogOut className="h-3.5 w-3.5" />
          </Button>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-7xl px-6 py-6">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
