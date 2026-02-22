"""Overnight performance report — run in the morning to see night results.

Usage:
    ssh root@178.156.247.90 "cd /opt/mec-radar && .venv/bin/python scripts/overnight_report.py"

Or locally (if DB is accessible):
    .venv/bin/python scripts/overnight_report.py
"""
import asyncio
import sys
from datetime import datetime, timedelta

from sqlalchemy import text

# Add project root to path
sys.path.insert(0, ".")
from src.db.database import async_session_factory


async def main() -> None:
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 12

    async with async_session_factory() as s:
        since = datetime.utcnow() - timedelta(hours=hours)

        # ── 1. Paper positions ──
        r = await s.execute(text("""
            SELECT p.id, t.symbol, t.address, t.source, t.first_seen_at,
                   p.entry_price, p.current_price, p.max_price,
                   p.amount_sol_invested, p.pnl_pct, p.pnl_usd,
                   p.status, p.close_reason, p.opened_at, p.closed_at,
                   s.score, s.token_mcap_at_signal, s.liquidity_at_signal, s.status as sig_status
            FROM positions p
            JOIN tokens t ON t.id = p.token_id
            LEFT JOIN signals s ON s.id = p.signal_id
            WHERE p.opened_at > :since AND p.is_paper = 1
            ORDER BY p.opened_at DESC
        """), {"since": since})
        positions = r.fetchall()

        print(f"\n{'='*120}")
        print(f"  OVERNIGHT REPORT — last {hours}h (since {since.strftime('%Y-%m-%d %H:%M')} UTC)")
        print(f"{'='*120}\n")

        # ── Summary ──
        total = len(positions)
        closed = [p for p in positions if p.status == "closed"]
        open_pos = [p for p in positions if p.status == "open"]
        winners = [p for p in closed if float(p.pnl_pct or 0) > 0]
        losers = [p for p in closed if float(p.pnl_pct or 0) <= 0]

        total_pnl_usd = sum(float(p.pnl_usd or 0) for p in closed)
        total_invested = sum(float(p.amount_sol_invested or 0) for p in positions)

        print(f"📊 PAPER POSITIONS: {total} total ({len(open_pos)} open, {len(closed)} closed)")
        print(f"   Winners: {len(winners)} | Losers: {len(losers)} | Win rate: {len(winners)*100/max(len(closed),1):.0f}%")
        print(f"   Total PnL: ${total_pnl_usd:+.2f} USD | Total invested: {total_invested:.2f} SOL")

        # ── Position details ──
        print(f"\n{'Symbol':<12} {'MCap':>10} {'Liq':>10} {'Score':>5} {'PnL%':>8} {'PnL$':>8} {'MaxX':>6} {'Status':<10} {'Reason':<15} {'Source':<12} {'Disc→Entry':>10}")
        print("-" * 120)

        for p in positions:
            mcap = f"${float(p.token_mcap_at_signal or 0):,.0f}" if p.token_mcap_at_signal else "N/A"
            liq = f"${float(p.liquidity_at_signal or 0):,.0f}" if p.liquidity_at_signal else "N/A"
            score = str(p.score) if p.score is not None else "?"

            pnl_pct = float(p.pnl_pct or 0)
            pnl_usd = float(p.pnl_usd or 0)

            # Sanity check for buggy PnL (rug artifacts)
            pnl_str = f"{pnl_pct:+.1f}%" if abs(pnl_pct) < 10000 else "BUG"
            pnl_usd_str = f"${pnl_usd:+.2f}" if abs(pnl_pct) < 10000 else "BUG"

            # Max multiplier
            if p.entry_price and p.max_price and float(p.entry_price) > 0:
                max_x = float(p.max_price) / float(p.entry_price)
            else:
                max_x = 0

            # Discovery → Entry delay
            if p.first_seen_at and p.opened_at:
                delay = (p.opened_at - p.first_seen_at).total_seconds()
                delay_str = f"{delay:.0f}s"
            else:
                delay_str = "?"

            status = p.status or "?"
            reason = p.close_reason or ("open" if status == "open" else "?")
            source = (p.source or "?")[:12]

            # Color indicators
            icon = "🟢" if pnl_pct > 0 else ("🔴" if pnl_pct < -20 else "🟡")
            if status == "open":
                icon = "⏳"

            print(f"{icon} {(p.symbol or '?'):<10} {mcap:>10} {liq:>10} {score:>5} {pnl_str:>8} {pnl_usd_str:>8} {max_x:>5.2f}x {status:<10} {reason:<15} {source:<12} {delay_str:>10}")

        # ── Top performers ──
        if closed:
            sorted_by_pnl = sorted(closed, key=lambda p: float(p.pnl_pct or 0), reverse=True)
            print(f"\n🏆 TOP PERFORMERS:")
            for p in sorted_by_pnl[:5]:
                pnl = float(p.pnl_pct or 0)
                if abs(pnl) < 10000:
                    print(f"   {p.symbol or '?':<12} {pnl:+.1f}%  (max {float(p.max_price or 0)/max(float(p.entry_price or 1), 1e-15):.2f}x)  reason={p.close_reason}")

            print(f"\n💀 WORST PERFORMERS:")
            for p in sorted_by_pnl[-5:]:
                pnl = float(p.pnl_pct or 0)
                if abs(pnl) < 10000:
                    print(f"   {p.symbol or '?':<12} {pnl:+.1f}%  reason={p.close_reason}")

        # ── 2. Real positions ──
        r2 = await s.execute(text("""
            SELECT p.id, t.symbol, t.address,
                   p.entry_price, p.current_price, p.max_price,
                   p.amount_sol_invested, p.pnl_pct, p.pnl_usd,
                   p.status, p.close_reason, p.opened_at, p.closed_at,
                   s.score, s.token_mcap_at_signal
            FROM positions p
            JOIN tokens t ON t.id = p.token_id
            LEFT JOIN signals s ON s.id = p.signal_id
            WHERE p.opened_at > :since AND p.is_paper = 0
            ORDER BY p.opened_at DESC
        """), {"since": since})
        real_positions = r2.fetchall()

        if real_positions:
            print(f"\n{'='*80}")
            print(f"  💰 REAL POSITIONS: {len(real_positions)}")
            print(f"{'='*80}")
            for p in real_positions:
                pnl = float(p.pnl_pct or 0)
                print(f"   {p.symbol or '?':<12} score={p.score} pnl={pnl:+.1f}% ${float(p.pnl_usd or 0):+.2f} status={p.status} reason={p.close_reason}")

        # ── 3. Signals without positions (missed opportunities) ──
        r3 = await s.execute(text("""
            SELECT s.id, t.symbol, t.address, t.source, t.first_seen_at,
                   s.score, s.status, s.token_mcap_at_signal, s.liquidity_at_signal,
                   s.token_price_at_signal, s.created_at,
                   s.peak_multiplier_after, s.peak_roi_pct, s.is_rug_after
            FROM signals s
            JOIN tokens t ON t.id = s.token_id
            WHERE s.created_at > :since
              AND s.status IN ('strong_buy', 'buy')
              AND NOT EXISTS (
                  SELECT 1 FROM positions p WHERE p.signal_id = s.id
              )
            ORDER BY s.created_at DESC
        """), {"since": since})
        missed = r3.fetchall()

        if missed:
            print(f"\n{'='*80}")
            print(f"  🚫 SIGNALS WITHOUT POSITIONS (missed): {len(missed)}")
            print(f"{'='*80}")
            for m in missed:
                mcap = f"${float(m.token_mcap_at_signal or 0):,.0f}" if m.token_mcap_at_signal else "?"
                peak = f"{float(m.peak_multiplier_after or 0):.2f}x" if m.peak_multiplier_after else "?"
                rug = " [RUG]" if m.is_rug_after else ""
                print(f"   {m.symbol or '?':<12} {m.status:<10} score={m.score} mcap={mcap} peak={peak}{rug}")

        # ── 4. All signals generated ──
        r4 = await s.execute(text("""
            SELECT s.status, COUNT(*) as cnt
            FROM signals s
            WHERE s.created_at > :since
            GROUP BY s.status
            ORDER BY cnt DESC
        """), {"since": since})
        sig_stats = r4.fetchall()

        print(f"\n{'='*80}")
        print(f"  📡 SIGNAL STATS (last {hours}h)")
        print(f"{'='*80}")
        for ss in sig_stats:
            print(f"   {ss.status:<15} {ss.cnt}")

        # ── 5. Pipeline throughput ──
        r5 = await s.execute(text("""
            SELECT COUNT(*) as cnt FROM tokens WHERE first_seen_at > :since
        """), {"since": since})
        total_tokens = r5.scalar()

        r6 = await s.execute(text("""
            SELECT COUNT(DISTINCT token_id) as cnt
            FROM token_snapshots
            WHERE timestamp > :since AND stage = 'INITIAL'
        """), {"since": since})
        initial_tokens = r6.scalar()

        r7 = await s.execute(text("""
            SELECT COUNT(*) as cnt FROM signals WHERE created_at > :since
        """), {"since": since})
        total_signals = r7.scalar()

        print(f"\n   Pipeline: {total_tokens} discovered → {initial_tokens} reached INITIAL → {total_signals} signals")
        prescan_rate = (total_tokens - initial_tokens) * 100 / max(total_tokens, 1)
        print(f"   PRE_SCAN rejection rate: {prescan_rate:.0f}%")

        # ── 6. Timing analysis (Disc → Signal) ──
        r8 = await s.execute(text("""
            SELECT t.symbol, t.first_seen_at, s.created_at as signal_at,
                   s.score, s.token_mcap_at_signal
            FROM signals s
            JOIN tokens t ON t.id = s.token_id
            WHERE s.created_at > :since AND s.status IN ('strong_buy', 'buy')
            ORDER BY s.created_at DESC
            LIMIT 20
        """), {"since": since})
        timing = r8.fetchall()

        if timing:
            print(f"\n{'='*80}")
            print(f"  ⏱️  TIMING: Discovery → Signal (buy/strong_buy)")
            print(f"{'='*80}")
            delays = []
            for t in timing:
                if t.first_seen_at and t.signal_at:
                    d = (t.signal_at - t.first_seen_at).total_seconds()
                    delays.append(d)
                    mcap = f"${float(t.token_mcap_at_signal or 0):,.0f}" if t.token_mcap_at_signal else "?"
                    print(f"   {t.symbol or '?':<12} score={t.score} mcap={mcap} delay={d:.0f}s ({d/60:.1f}m)")
            if delays:
                print(f"\n   Avg delay: {sum(delays)/len(delays):.0f}s ({sum(delays)/len(delays)/60:.1f}m)")
                print(f"   Min: {min(delays):.0f}s | Max: {max(delays):.0f}s | Median: {sorted(delays)[len(delays)//2]:.0f}s")

        print(f"\n{'='*120}")
        print(f"  Report generated at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"{'='*120}\n")


asyncio.run(main())
