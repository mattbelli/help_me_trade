"""Quick fraction sweep on the fixed 50-slot policy — where does CAGR top out?"""

from research._edgehunt.covid_and_rebalance import load_trades, simulate

trades = load_trades()
print(f"{'config':<20} {'CAGR':>7} {'maxDD':>7} {'sharpe':>7} {'avg inv':>8} {'taken':>12} {'final eq':>13}")
for frac in (0.05, 0.10, 0.15, 0.20, 0.25, 0.33, 0.50):
    r = simulate(trades, mode="fixed", per_trade_fraction=frac, max_concurrent=50)
    taken = f"{r['n_taken']}/{r['n_taken'] + r['n_skipped']}"
    print(
        f"fixed 50 x {frac * 100:>4.0f}%   {r['cagr'] * 100:>6.2f}% {r['max_dd'] * 100:>6.2f}% "
        f"{r['sharpe']:>7.2f} {r['avg_invested'] * 100:>7.1f}% {taken:>12} {r['final']:>13,.0f}"
    )
