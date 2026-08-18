//! Message-field access over decoded msgpack (`rmpv::Value`) plus the
//! timestamp conversions that mirror `signals/equity_stream/stream.py`.

use crate::pyfmt::iso_from_unix;
use rmpv::Value;

pub fn field<'a>(m: &'a Value, key: &str) -> Option<&'a Value> {
    m.as_map()?
        .iter()
        .find(|(k, _)| k.as_str() == Some(key))
        .map(|(_, v)| v)
}

pub fn str_field<'a>(m: &'a Value, key: &str) -> Option<&'a str> {
    field(m, key).and_then(Value::as_str)
}

/// Python `float(v)` for msgpack numbers.
pub fn as_f64(v: &Value) -> Option<f64> {
    match v {
        Value::F64(f) => Some(*f),
        Value::F32(f) => Some(*f as f64),
        Value::Integer(i) => i.as_i64().map(|x| x as f64).or_else(|| i.as_u64().map(|x| x as f64)),
        Value::String(s) => s.as_str().and_then(|s| s.trim().parse().ok()),
        _ => None,
    }
}

/// Python `int(v or 0)`; None if unparseable (caller decides).
pub fn as_i64(v: &Value) -> Option<i64> {
    match v {
        Value::Integer(i) => i.as_i64().or_else(|| i.as_u64().map(|x| x as i64)),
        Value::F64(f) => Some(f.trunc() as i64),
        Value::F32(f) => Some(f.trunc() as i64),
        Value::String(s) => s.as_str().and_then(|s| s.trim().parse().ok()),
        _ => None,
    }
}

/// msgpack Timestamp (ext -1) → `Timestamp.to_unix()` (float seconds), same
/// arithmetic as msgpack-python: `seconds + nanoseconds / 1e9`.
pub fn ts_to_unix(v: &Value) -> Option<f64> {
    match v {
        Value::Ext(-1, bytes) => {
            let (secs, nanos): (i64, u32) = match bytes.len() {
                4 => (u32::from_be_bytes(bytes[..4].try_into().ok()?) as i64, 0),
                8 => {
                    let raw = u64::from_be_bytes(bytes[..8].try_into().ok()?);
                    ((raw & 0x3_ffff_ffff) as i64, (raw >> 34) as u32)
                }
                12 => (
                    i64::from_be_bytes(bytes[4..12].try_into().ok()?),
                    u32::from_be_bytes(bytes[..4].try_into().ok()?),
                ),
                _ => return None,
            };
            Some(secs as f64 + nanos as f64 / 1e9)
        }
        _ => None,
    }
}

/// `ts_to_ms`: epoch ms; RFC3339 strings are accepted too (JSON path).
pub fn ts_to_ms(v: Option<&Value>) -> Option<f64> {
    let v = v?;
    if let Some(u) = ts_to_unix(v) {
        return Some(u * 1000.0);
    }
    if let Some(s) = v.as_str() {
        return chrono::DateTime::parse_from_rfc3339(s)
            .ok()
            .map(|d| d.timestamp_micros() as f64 / 1000.0);
    }
    None
}

/// `ts_to_iso`: RFC3339 with `+00:00`; empty string if unparseable/missing.
pub fn ts_to_iso(v: Option<&Value>) -> String {
    match v {
        Some(v) => match ts_to_unix(v) {
            Some(u) => iso_from_unix(u),
            None => match v {
                Value::Nil => String::new(),
                Value::String(s) => s.as_str().unwrap_or("").to_string(),
                other => other.to_string(),
            },
        },
        None => String::new(),
    }
}

/// `to_json_msg`: the message as JSON with Timestamps as RFC3339 — what the
/// API hub's JSON parser expects from real Alpaca.
pub fn to_json(m: &Value) -> serde_json::Value {
    match m {
        Value::Map(pairs) => serde_json::Value::Object(
            pairs
                .iter()
                .map(|(k, v)| (k.as_str().unwrap_or("").to_string(), to_json(v)))
                .collect(),
        ),
        Value::Array(items) => serde_json::Value::Array(items.iter().map(to_json).collect()),
        Value::Ext(-1, _) => serde_json::Value::String(ts_to_iso(Some(m))),
        Value::String(s) => serde_json::Value::String(s.as_str().unwrap_or("").to_string()),
        Value::F64(f) => serde_json::json!(*f),
        Value::F32(f) => serde_json::json!(*f as f64),
        Value::Integer(i) => i
            .as_i64()
            .map(serde_json::Value::from)
            .or_else(|| i.as_u64().map(serde_json::Value::from))
            .unwrap_or(serde_json::Value::Null),
        Value::Boolean(b) => serde_json::Value::Bool(*b),
        Value::Nil => serde_json::Value::Null,
        other => serde_json::Value::String(other.to_string()),
    }
}

pub fn pack(v: &Value) -> Vec<u8> {
    let mut buf = Vec::new();
    rmpv::encode::write_value(&mut buf, v).expect("msgpack encode");
    buf
}

pub fn map(pairs: Vec<(&str, Value)>) -> Value {
    Value::Map(pairs.into_iter().map(|(k, v)| (Value::from(k), v)).collect())
}
