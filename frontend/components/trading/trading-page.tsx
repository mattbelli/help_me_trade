"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { TickerSearch } from "@/components/trading/ticker-search";
import { TvLightChart } from "@/components/trading/tv-light-chart";
import { requestAlertNotifications } from "@/components/trading/price-alert-monitor";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  createPriceAlert,
  deletePriceAlert,
  fetchPriceAlerts,
  fetchWatchlist,
  fetchWatchlists,
  type PriceAlert,
} from "@/lib/api";

const DIRECTIONS = [
  { value: "cross", label: "Crosses" },
  { value: "above", label: "Above" },
  { value: "below", label: "Below" },
] as const;

export function TradingPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const paramSymbol = searchParams.get("symbol")?.trim().toUpperCase() ?? "";

  const [symbol, setSymbol] = useState("SPY");
  const [searchQuery, setSearchQuery] = useState("");
  const [alertPrice, setAlertPrice] = useState("");
  const [alertDirection, setAlertDirection] =
    useState<(typeof DIRECTIONS)[number]["value"]>("cross");
  const [alertRepeat, setAlertRepeat] = useState(false);
  const [alerts, setAlerts] = useState<PriceAlert[]>([]);
  const [alertError, setAlertError] = useState<string | null>(null);
  const [notificationsOn, setNotificationsOn] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (typeof Notification !== "undefined") {
      setNotificationsOn(Notification.permission === "granted");
    }
  }, []);

  useEffect(() => {
    fetchWatchlists()
      .then((data) => {
        const active = data.lists.find((list) => list.id === data.active_id);
        const list = active?.symbols ?? [];
        if (paramSymbol) {
          setSymbol(paramSymbol);
        } else if (list[0]) {
          setSymbol(list[0]);
          router.replace(`/trading?symbol=${encodeURIComponent(list[0])}`, {
            scroll: false,
          });
        }
      })
      .catch(() =>
        fetchWatchlist().then((list) => {
          if (paramSymbol) {
            setSymbol(paramSymbol);
          } else if (list[0]) {
            setSymbol(list[0]);
            router.replace(`/trading?symbol=${encodeURIComponent(list[0])}`, {
              scroll: false,
            });
          }
        }),
      )
      .finally(() => setReady(true));
  }, [paramSymbol, router]);

  useEffect(() => {
    if (paramSymbol) setSymbol(paramSymbol);
  }, [paramSymbol]);

  useEffect(() => {
    function load() {
      fetchPriceAlerts(symbol)
        .then(setAlerts)
        .catch(() => setAlerts([]));
    }
    load();
    const id = setInterval(load, 15_000);
    return () => clearInterval(id);
  }, [symbol]);

  function selectFromSearch(sym: string) {
    const upper = sym.trim().toUpperCase();
    if (!upper) return;
    setSymbol(upper);
    setSearchQuery(upper);
    router.replace(`/trading?symbol=${encodeURIComponent(upper)}`, {
      scroll: false,
    });
  }

  async function addAlert() {
    const price = Number(alertPrice.trim());
    if (!Number.isFinite(price)) {
      setAlertError("Enter a valid price");
      return;
    }
    setAlertError(null);
    try {
      const created = await createPriceAlert({
        symbol,
        price,
        direction: alertDirection,
        repeat: alertRepeat,
      });
      setAlerts((prev) => [created, ...prev]);
      setAlertPrice("");
    } catch {
      setAlertError("Could not save alert");
    }
  }

  async function removeAlert(id: number) {
    try {
      await deletePriceAlert(id);
      setAlerts((prev) => prev.filter((a) => a.id !== id));
    } catch {
      // ignore
    }
  }

  async function enableNotifications() {
    const ok = await requestAlertNotifications();
    setNotificationsOn(ok);
  }

  if (!ready) {
    return <Skeleton className="h-[520px] w-full rounded-lg" />;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Trading"
        subtitle="Search any US equity ticker. Watchlist lives in the right panel."
      />

      <div className="grid gap-6 xl:grid-cols-[1fr_280px]">
        <Card className="overflow-hidden border-border/80">
          <CardHeader className="flex flex-col gap-4 border-b border-border/80 pb-4 sm:flex-row sm:items-end sm:justify-between">
            <div className="min-w-0 flex-1 space-y-3">
              <div>
                <CardTitle className="text-base font-medium tracking-tight">
                  {symbol}
                </CardTitle>
                <CardDescription>
                  Lightweight Charts · Alpaca IEX · volume + ET time axis
                </CardDescription>
              </div>
              <div className="flex max-w-sm gap-2">
                <TickerSearch
                  value={searchQuery}
                  onChange={setSearchQuery}
                  onSelect={selectFromSearch}
                  className="flex-1"
                />
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => selectFromSearch(searchQuery)}
                >
                  Go
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            <TvLightChart symbol={symbol} />
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card className="border-border/80">
            <CardHeader>
              <CardTitle className="text-base">Order ticket</CardTitle>
              <CardDescription>
                Read-only — order placement not built yet
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-2 gap-2">
                <Button disabled>Buy</Button>
                <Button disabled variant="outline">
                  Sell
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                Backend only reads account, positions, and orders today.
              </p>
            </CardContent>
          </Card>

          <Card className="border-border/80">
            <CardHeader>
              <CardTitle className="text-base">Price alerts</CardTitle>
              <CardDescription>
                Live monitoring via Alpaca stream · lines on chart
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap gap-1">
                {DIRECTIONS.map((d) => (
                  <Button
                    key={d.value}
                    type="button"
                    size="sm"
                    variant={alertDirection === d.value ? "secondary" : "ghost"}
                    className="h-8 px-2 text-xs"
                    onClick={() => setAlertDirection(d.value)}
                  >
                    {d.label}
                  </Button>
                ))}
              </div>
              <div className="flex gap-2">
                <Input
                  placeholder="e.g. 145.50"
                  value={alertPrice}
                  onChange={(e) => setAlertPrice(e.target.value)}
                  className="tabular-nums"
                />
                <Button type="button" variant="secondary" onClick={addAlert}>
                  Add
                </Button>
              </div>
              <Button
                type="button"
                size="sm"
                variant={alertRepeat ? "secondary" : "ghost"}
                className="h-8 w-full justify-start px-2 text-xs"
                onClick={() => setAlertRepeat((v) => !v)}
                title="Persistent alerts re-arm after each crossing and stay until you remove them. One-shot alerts fire once."
              >
                {alertRepeat ? "↻ Repeat (stays until removed)" : "→ One-shot (fires once)"}
              </Button>
              {alertError ? (
                <p className="text-xs text-destructive">{alertError}</p>
              ) : null}
              {!notificationsOn ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="w-full text-xs"
                  onClick={enableNotifications}
                >
                  Enable desktop notifications
                </Button>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Desktop notifications enabled
                </p>
              )}
              {alerts.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No alerts yet for {symbol}.
                </p>
              ) : (
                <ul className="space-y-2">
                  {alerts.map((a) => (
                    <li
                      key={a.id}
                      className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-sm"
                    >
                      <span className="font-medium">{a.symbol}</span>
                      <span className="text-xs text-muted-foreground">
                        {a.direction}
                      </span>
                      <span className="tabular-nums text-muted-foreground">
                        @ {a.price.toFixed(2)}
                      </span>
                      <span
                        className="text-xs text-muted-foreground"
                        title={
                          a.repeat
                            ? "Persistent — re-arms after each crossing"
                            : "One-shot — fires once"
                        }
                      >
                        {a.repeat ? "↻" : "→"}
                      </span>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-7 text-xs"
                        onClick={() => removeAlert(a.id)}
                      >
                        Remove
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      <Tabs defaultValue="levels" className="w-full">
        <TabsList>
          <TabsTrigger value="levels">Levels</TabsTrigger>
          <TabsTrigger value="notes">Notes</TabsTrigger>
        </TabsList>
        <TabsContent value="levels">
          <Card className="border-border/80">
            <CardContent className="py-6 text-sm text-muted-foreground">
              Horizontal alert lines appear on the chart. Intraday bars are
              regular-session IEX data — extended hours are not shown on the
              free Alpaca tier.
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="notes">
          <Card className="border-border/80">
            <CardContent className="py-6 text-sm text-muted-foreground">
              Trade journal entries tied to this symbol will live here.
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
