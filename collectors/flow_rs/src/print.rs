//! The normalized print — `intraday.normalize_print` shape plus the fields the
//! sweep cycle stamps (`direction`, `direction_basis`, `mid_side`, spread tags).
//! Condition codes are always empty on this lane until the pairing sidecar
//! (stage 2) stamps them, so the hygiene / multileg / ISO gates are constant
//! false here — kept as explicit fields so stage 2 is a data change, not a
//! logic change.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Dir {
    Buy,
    Sell,
    Undetermined,
}

impl Dir {
    pub fn as_str(self) -> &'static str {
        match self {
            Dir::Buy => "buy_side",
            Dir::Sell => "sell_side",
            Dir::Undetermined => "undetermined",
        }
    }
}

#[derive(Clone, Debug)]
pub struct Print {
    pub occ: String,
    pub underlying: String,
    pub right: &'static str, // "call" | "put"
    pub strike: f64,
    pub expiry: String,
    pub dte: i64,
    pub ts_ms: i64,
    pub price: f64,
    pub size: i64,
    pub exchange: String, // str(publisher_id)
    pub conditions: Vec<String>,
    /// (bid, ask, ts_recv // 1_000_000) when the wire book was defined and 0 < bid < ask.
    pub nbbo: Option<(f64, f64, i64)>,
    // stamped by the sweep cycle
    pub direction: Dir,
    pub basis: &'static str,    // "dbn_nbbo" | "none"
    pub mid_side: &'static str, // buy | sell | unclassified | wide_spread
    pub spread_leg: bool,
    pub likely_spread: bool,
}

impl Print {
    pub fn premium(&self) -> f64 {
        self.size as f64 * self.price * 100.0
    }
    /// F-1: cancels / late-OOS / extended-hours codes.
    pub fn hygiene_excluded(&self) -> bool {
        self.conditions.iter().any(|c| {
            matches!(
                c.as_str(),
                "A" | "C" | "E" | "G" | "B" | "D" | "F" | "H" | "v"
            )
        })
    }
    /// F-2: MLET + combo codes `fghijklmnopqrs`.
    pub fn multileg(&self) -> bool {
        self.conditions
            .iter()
            .any(|c| c.len() == 1 && "fghijklmnopqrs".contains(c.as_str()))
    }
    pub fn spread_tagged(&self) -> bool {
        self.spread_leg || self.likely_spread
    }
    pub fn iso_flagged(&self) -> bool {
        self.conditions.iter().any(|c| c == "S")
    }
    pub fn right_letter(&self) -> char {
        if self.right.starts_with('c') {
            'c'
        } else {
            'p'
        }
    }
}

pub const INDEX_MEGACAP: [&str; 6] = ["SPY", "QQQ", "IWM", "DIA", "SPX", "SPXW"];

pub fn is_index_megacap(sym: &str) -> bool {
    INDEX_MEGACAP.contains(&sym)
}
