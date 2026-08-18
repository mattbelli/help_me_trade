//! Raw-frame tee (copy of collectors/equity_rs/src/record.rs) — the same tape
//! `signals/equity_stream/recorder.py` writes
//! (zstd stream of msgpack `[recv_ns, frame_bytes]`, one file per ET date,
//! appended across restarts), so `tools/equity_tape_replay.py` /
//! `tools/equity_ab.sh` read a Rust-recorded session unchanged. Off the hot
//! path: a bounded channel to a writer thread; overflow drops the newest frame
//! and counts it — the tape never waits on disk.

use chrono::{Datelike, TimeZone};
use chrono_tz::America::New_York;
use rmpv::Value;
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, SyncSender, TrySendError};
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::{SystemTime, UNIX_EPOCH};

const QUEUE_MAX: usize = 200_000; // frames; ~a few seconds of the close burst
const ZSTD_LEVEL: i32 = 3;

pub struct Recorder {
    tx: SyncSender<(u64, Vec<u8>)>,
    handle: Option<JoinHandle<()>>,
    pub frames: Arc<AtomicU64>,
    pub drops: AtomicU64,
}

impl Recorder {
    pub fn start(dir: PathBuf) -> std::io::Result<Self> {
        std::fs::create_dir_all(&dir)?;
        let (tx, rx) = mpsc::sync_channel::<(u64, Vec<u8>)>(QUEUE_MAX);
        let frames = Arc::new(AtomicU64::new(0));
        let f = frames.clone();
        let handle = std::thread::Builder::new()
            .name("equity-tape-recorder".into())
            .spawn(move || run(dir, rx, f))?;
        Ok(Self { tx, handle: Some(handle), frames, drops: AtomicU64::new(0) })
    }

    /// Recv-loop safe: copies the frame into the queue or drops it.
    pub fn record(&self, raw: &[u8]) {
        let recv_ns = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos() as u64).unwrap_or(0);
        match self.tx.try_send((recv_ns, raw.to_vec())) {
            Ok(()) => {}
            Err(TrySendError::Full(_)) | Err(TrySendError::Disconnected(_)) => {
                self.drops.fetch_add(1, Ordering::Relaxed);
            }
        }
    }

    pub fn stop(mut self) -> (u64, u64) {
        drop(self.tx);
        if let Some(h) = self.handle.take() {
            let _ = h.join();
        }
        (self.frames.load(Ordering::Relaxed), self.drops.load(Ordering::Relaxed))
    }
}

struct Sink {
    path: PathBuf,
    enc: zstd::Encoder<'static, File>,
}

fn run(dir: PathBuf, rx: Receiver<(u64, Vec<u8>)>, frames: Arc<AtomicU64>) {
    let mut sink: Option<Sink> = None;
    let mut buf: Vec<u8> = Vec::with_capacity(1 << 16);
    while let Ok((recv_ns, raw)) = rx.recv() {
        let day = New_York.timestamp_nanos(recv_ns as i64).date_naive();
        let path = dir.join(format!("{:04}-{:02}-{:02}.mpk.zst", day.year(), day.month(), day.day()));
        if sink.as_ref().map(|s| s.path != path).unwrap_or(true) {
            if let Some(s) = sink.take() {
                let _ = s.enc.finish().and_then(|mut f| f.flush());
            }
            match OpenOptions::new().create(true).append(true).open(&path)
                .and_then(|f| zstd::Encoder::new(f, ZSTD_LEVEL))
            {
                Ok(enc) => {
                    eprintln!("equity tape recorder: writing {}", path.display());
                    sink = Some(Sink { path, enc });
                }
                Err(e) => {
                    eprintln!("equity tape recorder: open {} failed: {e} — recording stops", path.display());
                    return;
                }
            }
        }
        let s = sink.as_mut().expect("sink set above");
        buf.clear();
        let item = Value::Array(vec![Value::from(recv_ns), Value::Binary(raw)]);
        if rmpv::encode::write_value(&mut buf, &item).is_err() {
            continue;
        }
        if let Err(e) = s.enc.write_all(&buf) {
            eprintln!("equity tape recorder: write failed: {e} — recording stops");
            return;
        }
        frames.fetch_add(1, Ordering::Relaxed);
    }
    if let Some(s) = sink.take() {
        let _ = s.enc.finish().and_then(|mut f| f.flush());
    }
}
