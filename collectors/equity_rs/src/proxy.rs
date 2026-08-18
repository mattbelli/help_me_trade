//! Local Alpaca-protocol proxy — hands the API hub its slice of the tape.
//! Port of `signals/equity_stream/proxy.py`: loopback JSON websocket, auth
//! accepted unconditionally, per-client subscriptions, union view for the hot
//! path, queue-per-client with drop-on-overflow, refuse/drop clients while the
//! upstream socket is down.

use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value as Json};
use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, RwLock};
use tokio::net::TcpListener;
use tokio::sync::{mpsc, watch};
use tokio_tungstenite::tungstenite::Message;

pub const CHANNELS: [&str; 5] = ["trades", "quotes", "dailyBars", "bars", "updatedBars"];
pub const CH_TRADES: usize = 0;
pub const CH_QUOTES: usize = 1;
pub const CH_DAILY: usize = 2;
pub const CH_BARS: usize = 3;
pub const CH_UPDATED: usize = 4;
/// Channels the collector subscribes upstream PER SYMBOL (trades/quotes are wildcard).
pub const PER_SYMBOL_CHANNELS: [usize; 3] = [CH_DAILY, CH_BARS, CH_UPDATED];
pub const CLIENT_QUEUE_MAX: usize = 2000;
const SEND_BATCH_MAX: usize = 128;

pub type Subs = [HashSet<String>; 5];

/// Union of per-symbol channel wants → the hot task diff-subscribes upstream.
#[derive(Debug, Clone)]
pub struct ChanSyms {
    pub channel: usize,
    pub symbols: HashSet<String>,
}

struct Client {
    subs: Subs,
    tx: mpsc::Sender<Arc<str>>,
    drops: Arc<AtomicU64>,
}

pub struct Proxy {
    unions: RwLock<Subs>,
    clients: Mutex<HashMap<u64, Client>>,
    next_id: AtomicU64,
    healthy_tx: watch::Sender<bool>,
    chan_tx: mpsc::UnboundedSender<ChanSyms>,
    pub total_drops: AtomicU64,
}

impl Proxy {
    pub fn new(chan_tx: mpsc::UnboundedSender<ChanSyms>) -> Arc<Self> {
        let (healthy_tx, _) = watch::channel(false);
        Arc::new(Self {
            unions: RwLock::new(Default::default()),
            clients: Mutex::new(HashMap::new()),
            next_id: AtomicU64::new(1),
            healthy_tx,
            chan_tx,
            total_drops: AtomicU64::new(0),
        })
    }

    // -- hot path ---------------------------------------------------------------------

    pub fn wants(&self, channel: usize, symbol: &str) -> bool {
        self.unions.read().unwrap()[channel].contains(symbol)
    }

    pub fn publish(&self, channel: usize, symbol: &str, msg: &Json) {
        let text: Arc<str> = Arc::from(msg.to_string());
        let clients = self.clients.lock().unwrap();
        for c in clients.values() {
            if c.subs[channel].contains(symbol) && c.tx.try_send(text.clone()).is_err() {
                c.drops.fetch_add(1, Ordering::Relaxed);
                self.total_drops.fetch_add(1, Ordering::Relaxed);
            }
        }
    }

    pub fn client_count(&self) -> usize {
        self.clients.lock().unwrap().len()
    }

    pub fn union_size(&self, channel: usize) -> usize {
        self.unions.read().unwrap()[channel].len()
    }

    // -- upstream health -----------------------------------------------------------------

    pub fn set_upstream_healthy(&self, healthy: bool) {
        self.healthy_tx.send_replace(healthy);
    }

    pub fn upstream_healthy(&self) -> bool {
        *self.healthy_tx.borrow()
    }

    fn rebuild_unions(&self) {
        let mut unions: Subs = Default::default();
        {
            let clients = self.clients.lock().unwrap();
            for c in clients.values() {
                for (i, set) in c.subs.iter().enumerate() {
                    unions[i].extend(set.iter().cloned());
                }
            }
        }
        for ch in PER_SYMBOL_CHANNELS {
            let _ = self.chan_tx.send(ChanSyms { channel: ch, symbols: unions[ch].clone() });
        }
        *self.unions.write().unwrap() = unions;
    }

    // -- server ---------------------------------------------------------------------------

    pub async fn serve(self: Arc<Self>, host: &str, port: u16) -> anyhow::Result<()> {
        let listener = TcpListener::bind((host, port)).await?;
        eprintln!("local market proxy listening on ws://{host}:{port}");
        loop {
            let (stream, _) = listener.accept().await?;
            let me = self.clone();
            tokio::spawn(async move {
                let ws = match tokio_tungstenite::accept_async(stream).await {
                    Ok(ws) => ws,
                    Err(_) => return, // the API's TCP health probe, not a client
                };
                me.handle(ws).await;
            });
        }
    }

    async fn handle(self: Arc<Self>, ws: tokio_tungstenite::WebSocketStream<tokio::net::TcpStream>) {
        let (mut sink, mut source) = ws.split();
        let mut healthy_rx = self.healthy_tx.subscribe();
        if !*healthy_rx.borrow() {
            let _ = sink
                .send(Message::Text(json!([{"T": "error", "code": 503, "msg": "upstream not connected"}]).to_string()))
                .await;
            let _ = sink.close().await;
            return;
        }
        let (tx, mut rx) = mpsc::channel::<Arc<str>>(CLIENT_QUEUE_MAX);
        let drops = Arc::new(AtomicU64::new(0));
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        self.clients.lock().unwrap().insert(
            id,
            Client { subs: Default::default(), tx, drops: drops.clone() },
        );
        // Sender: drain the queue; batch whatever is ready into one frame.
        let (out_tx, mut out_rx) = mpsc::channel::<String>(64);
        let sender = tokio::spawn(async move {
            while let Some(first) = rx.recv().await {
                let mut batch: Vec<Arc<str>> = vec![first];
                while batch.len() < SEND_BATCH_MAX {
                    match rx.try_recv() {
                        Ok(m) => batch.push(m),
                        Err(_) => break,
                    }
                }
                let mut s = String::with_capacity(batch.iter().map(|b| b.len() + 1).sum::<usize>() + 2);
                s.push('[');
                for (i, b) in batch.iter().enumerate() {
                    if i > 0 {
                        s.push(',');
                    }
                    s.push_str(b);
                }
                s.push(']');
                if out_tx.send(s).await.is_err() {
                    break;
                }
            }
        });
        let _ = sink.send(Message::Text(json!([{"T": "success", "msg": "connected"}]).to_string())).await;
        loop {
            tokio::select! {
                Some(frame) = out_rx.recv() => {
                    if sink.send(Message::Text(frame)).await.is_err() { break; }
                }
                changed = healthy_rx.changed() => {
                    if changed.is_err() || !*healthy_rx.borrow() { break; }
                }
                msg = source.next() => {
                    let Some(Ok(msg)) = msg else { break };
                    let raw = match msg {
                        Message::Text(t) => t,
                        Message::Binary(b) => String::from_utf8_lossy(&b).into_owned(),
                        Message::Close(_) => break,
                        _ => continue,
                    };
                    for m in parse_client_frame(&raw) {
                        let action = m.get("action").and_then(Json::as_str).unwrap_or("").to_lowercase();
                        if action == "auth" {
                            let _ = sink.send(Message::Text(json!([{"T": "success", "msg": "authenticated"}]).to_string())).await;
                        } else if action == "subscribe" || action == "unsubscribe" {
                            let ack = self.apply_subscription(id, &action, &m);
                            let _ = sink.send(Message::Text(Json::Array(vec![ack]).to_string())).await;
                        }
                    }
                }
            }
        }
        self.clients.lock().unwrap().remove(&id);
        sender.abort();
        self.rebuild_unions();
        let _ = sink.close().await;
        let _ = drops; // per-client count folded into total_drops already
    }

    fn apply_subscription(&self, id: u64, action: &str, msg: &Json) -> Json {
        let mut clients = self.clients.lock().unwrap();
        let Some(c) = clients.get_mut(&id) else { return json!({"T": "subscription"}) };
        for (i, ch) in CHANNELS.iter().enumerate() {
            let Some(raw) = msg.get(*ch).and_then(Json::as_array) else { continue };
            let symbols: HashSet<String> = raw
                .iter()
                .filter_map(Json::as_str)
                .map(|s| s.trim().to_uppercase())
                .filter(|s| !s.is_empty())
                .collect();
            if action == "subscribe" {
                c.subs[i].extend(symbols);
            } else {
                for s in symbols {
                    c.subs[i].remove(&s);
                }
            }
        }
        let mut ack = serde_json::Map::new();
        ack.insert("T".into(), "subscription".into());
        for (i, ch) in CHANNELS.iter().enumerate() {
            let mut v: Vec<&String> = c.subs[i].iter().collect();
            v.sort();
            ack.insert((*ch).into(), json!(v));
        }
        drop(clients);
        self.rebuild_unions();
        Json::Object(ack)
    }
}

fn parse_client_frame(raw: &str) -> Vec<Json> {
    match serde_json::from_str::<Json>(raw) {
        Ok(Json::Object(o)) => vec![Json::Object(o)],
        Ok(Json::Array(a)) => a.into_iter().filter(|v| v.is_object()).collect(),
        _ => vec![],
    }
}
