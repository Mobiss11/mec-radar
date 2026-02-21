import { useCallback, useEffect, useState } from "react"
import { usePolling } from "@/hooks/use-polling"
import { useAuth } from "@/hooks/use-auth"
import { settings } from "@/lib/api"
import { Switch } from "@/components/ui/switch"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { StatusDot } from "@/components/common/status-dot"
import { Separator } from "@/components/ui/separator"
import { Save, Check } from "lucide-react"

export function SettingsPage() {
  const { csrfToken, refreshCsrf } = useAuth()
  const [localSettings, setLocalSettings] = useState<Record<string, unknown> | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const { data: serverSettings, loading: settLoading } = usePolling({
    fetcher: settings.get,
    interval: 30000,
  })

  const { data: apiStatus, loading: apiLoading } = usePolling({
    fetcher: settings.apiStatus,
    interval: 30000,
  })

  useEffect(() => {
    if (serverSettings && !localSettings) {
      setLocalSettings({ ...serverSettings })
    }
  }, [serverSettings, localSettings])

  const handleToggle = useCallback(
    (key: string, value: boolean) => {
      setLocalSettings((prev) => (prev ? { ...prev, [key]: value } : null))
    },
    [],
  )

  const handleNumber = useCallback(
    (key: string, value: string) => {
      const num = parseFloat(value)
      if (!isNaN(num)) {
        setLocalSettings((prev) => (prev ? { ...prev, [key]: num } : null))
      }
    },
    [],
  )

  const handleSave = useCallback(async () => {
    if (!localSettings) return
    setSaving(true)
    try {
      const token = csrfToken ?? (await refreshCsrf())
      // Only send changed fields
      const changed: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(localSettings)) {
        if (serverSettings && v !== (serverSettings as Record<string, unknown>)[k]) {
          changed[k] = v
        }
      }
      if (Object.keys(changed).length > 0) {
        await settings.update(changed, token)
      }
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      alert(e instanceof Error ? e.message : "Save failed")
    } finally {
      setSaving(false)
    }
  }, [localSettings, serverSettings, csrfToken, refreshCsrf])

  const services = ((apiStatus as Record<string, unknown>)?.services ?? []) as Array<{
    name: string
    configured: boolean
    enabled: boolean
  }>

  const ls = localSettings as Record<string, unknown> | null

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
          <p className="text-sm text-muted-foreground">
            Runtime configuration — changes apply immediately
          </p>
        </div>
        <Button
          onClick={handleSave}
          disabled={saving || !localSettings}
          className="gap-2"
        >
          {saved ? (
            <>
              <Check className="h-4 w-4" /> Saved
            </>
          ) : (
            <>
              <Save className="h-4 w-4" /> Save Changes
            </>
          )}
        </Button>
      </div>

      {settLoading && !localSettings ? (
        <div className="space-y-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-12 rounded-lg" />
          ))}
        </div>
      ) : ls ? (
        <div className="space-y-6">
          {/* Feature Flags */}
          <SettingsSection title="Feature Flags">
            <ToggleRow
              label="Birdeye"
              value={ls.enable_birdeye as boolean}
              onChange={(v) => handleToggle("enable_birdeye", v)}
            />
            <ToggleRow
              label="GMGN"
              value={ls.enable_gmgn as boolean}
              onChange={(v) => handleToggle("enable_gmgn", v)}
            />
            <ToggleRow
              label="PumpPortal"
              value={ls.enable_pumpportal as boolean}
              onChange={(v) => handleToggle("enable_pumpportal", v)}
            />
            <ToggleRow
              label="DexScreener"
              value={ls.enable_dexscreener as boolean}
              onChange={(v) => handleToggle("enable_dexscreener", v)}
            />
            <ToggleRow
              label="Meteora DBC"
              value={ls.enable_meteora_dbc as boolean}
              onChange={(v) => handleToggle("enable_meteora_dbc", v)}
            />
            <ToggleRow
              label="gRPC Streaming"
              value={ls.enable_grpc_streaming as boolean}
              onChange={(v) => handleToggle("enable_grpc_streaming", v)}
            />
            <ToggleRow
              label="SolSniffer"
              value={ls.enable_solsniffer as boolean}
              onChange={(v) => handleToggle("enable_solsniffer", v)}
            />
            <ToggleRow
              label="Twitter"
              value={ls.enable_twitter as boolean}
              onChange={(v) => handleToggle("enable_twitter", v)}
            />
            <ToggleRow
              label="Telegram Checker"
              value={ls.enable_telegram_checker as boolean}
              onChange={(v) => handleToggle("enable_telegram_checker", v)}
            />
            <ToggleRow
              label="LLM Analysis"
              value={ls.enable_llm_analysis as boolean}
              onChange={(v) => handleToggle("enable_llm_analysis", v)}
            />
          </SettingsSection>

          {/* Paper Trading */}
          <SettingsSection title="Paper Trading">
            <ToggleRow
              label="Paper Trading Enabled"
              value={ls.paper_trading_enabled as boolean}
              onChange={(v) => handleToggle("paper_trading_enabled", v)}
            />
            <NumberRow
              label="SOL per Trade"
              value={ls.paper_sol_per_trade as number}
              onChange={(v) => handleNumber("paper_sol_per_trade", v)}
              min={0.01}
              max={100}
              step={0.1}
            />
            <NumberRow
              label="Max Positions"
              value={ls.paper_max_positions as number}
              onChange={(v) => handleNumber("paper_max_positions", v)}
              min={1}
              max={50}
              step={1}
            />
            <NumberRow
              label="Take Profit (x)"
              value={ls.paper_take_profit_x as number}
              onChange={(v) => handleNumber("paper_take_profit_x", v)}
              min={1.5}
              max={50}
              step={0.5}
            />
            <NumberRow
              label="Stop Loss (%)"
              value={ls.paper_stop_loss_pct as number}
              onChange={(v) => handleNumber("paper_stop_loss_pct", v)}
              min={-90}
              max={-5}
              step={5}
            />
            <NumberRow
              label="Timeout (hours)"
              value={ls.paper_timeout_hours as number}
              onChange={(v) => handleNumber("paper_timeout_hours", v)}
              min={1}
              max={72}
              step={1}
            />
          </SettingsSection>

          {/* Signal Decay */}
          <SettingsSection title="Signal Decay">
            <ToggleRow
              label="Signal Decay Enabled"
              value={ls.signal_decay_enabled as boolean}
              onChange={(v) => handleToggle("signal_decay_enabled", v)}
            />
            <NumberRow
              label="Strong Buy TTL (hours)"
              value={ls.signal_strong_buy_ttl_hours as number}
              onChange={(v) => handleNumber("signal_strong_buy_ttl_hours", v)}
              min={1}
              max={48}
              step={1}
            />
            <NumberRow
              label="Buy TTL (hours)"
              value={ls.signal_buy_ttl_hours as number}
              onChange={(v) => handleNumber("signal_buy_ttl_hours", v)}
              min={1}
              max={72}
              step={1}
            />
            <NumberRow
              label="Watch TTL (hours)"
              value={ls.signal_watch_ttl_hours as number}
              onChange={(v) => handleNumber("signal_watch_ttl_hours", v)}
              min={1}
              max={168}
              step={1}
            />
          </SettingsSection>
        </div>
      ) : null}

      {/* API Status Grid */}
      <Separator />
      <div>
        <h2 className="mb-3 text-base font-semibold">API Services</h2>
        {apiLoading ? (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-14 rounded-xl" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
            {services.map((svc) => (
              <div
                key={svc.name}
                className="flex items-center gap-3 rounded-xl border border-border/50 bg-card/60 p-3 backdrop-blur-sm"
              >
                <StatusDot
                  status={
                    svc.configured && svc.enabled
                      ? "connected"
                      : svc.configured
                        ? "disabled"
                        : "disconnected"
                  }
                />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium truncate">{svc.name}</p>
                  <p className="text-[10px] text-muted-foreground">
                    {svc.configured ? "Configured" : "Not configured"}
                    {svc.configured && !svc.enabled && " (disabled)"}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function SettingsSection({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-xl border border-border/50 bg-card/60 p-4 backdrop-blur-sm">
      <h3 className="mb-4 text-sm font-medium uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      <div className="space-y-3">{children}</div>
    </div>
  )
}

function ToggleRow({
  label,
  value,
  onChange,
}: {
  label: string
  value: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <div className="flex items-center justify-between">
      <Label className="text-sm">{label}</Label>
      <Switch checked={value} onCheckedChange={onChange} />
    </div>
  )
}

function NumberRow({
  label,
  value,
  onChange,
  min,
  max,
  step,
}: {
  label: string
  value: number
  onChange: (v: string) => void
  min: number
  max: number
  step: number
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <Label className="text-sm">{label}</Label>
      <Input
        type="number"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        min={min}
        max={max}
        step={step}
        className="w-24 text-right font-data"
      />
    </div>
  )
}
