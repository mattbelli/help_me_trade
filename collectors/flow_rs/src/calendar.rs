//! NYSE session calendar — a port of `trading/market_calendar.py` (`is_trading_day`,
//! `is_early_close`) and `trading/market_hours.is_market_open`. Same algorithm, same
//! ad-hoc closure list; keep the two in step when a closure is appended there.

use chrono::{Datelike, NaiveDate, Timelike, Weekday};

const AD_HOC_CLOSURES: [(i32, u32, u32); 4] = [
    (2012, 10, 29), // Hurricane Sandy
    (2012, 10, 30), // Hurricane Sandy
    (2018, 12, 5),  // National day of mourning — George H. W. Bush
    (2025, 1, 9),   // National day of mourning — Jimmy Carter
];
const MLK_FIRST_YEAR: i32 = 1998;
const JUNETEENTH_FIRST_YEAR: i32 = 2022;

fn ymd(y: i32, m: u32, d: u32) -> NaiveDate {
    NaiveDate::from_ymd_opt(y, m, d).expect("valid date")
}

/// The n-th `weekday` of a month (Python weekday numbering: 0 = Monday).
fn nth_weekday(year: i32, month: u32, weekday: u32, n: u32) -> NaiveDate {
    let first = ymd(year, month, 1);
    let offset = (weekday as i64 - first.weekday().num_days_from_monday() as i64).rem_euclid(7);
    first + chrono::Duration::days(offset + 7 * (n as i64 - 1))
}

fn last_weekday(year: i32, month: u32, weekday: u32) -> NaiveDate {
    let nxt = if month == 12 {
        ymd(year + 1, 1, 1)
    } else {
        ymd(year, month + 1, 1)
    };
    let last = nxt - chrono::Duration::days(1);
    let back = (last.weekday().num_days_from_monday() as i64 - weekday as i64).rem_euclid(7);
    last - chrono::Duration::days(back)
}

/// Gregorian Easter Sunday (anonymous Computus) — same arithmetic as the Python.
fn easter(year: i32) -> NaiveDate {
    let a = year % 19;
    let (b, c) = (year / 100, year % 100);
    let (d, e) = (b / 4, b % 4);
    let f = (b + 8) / 25;
    let g = (b - f + 1) / 3;
    let h = (19 * a + b - d - g + 15) % 30;
    let (i, k) = (c / 4, c % 4);
    let m = (32 + 2 * e + 2 * i - h - k) % 7;
    let n = (a + 11 * h + 22 * m) / 451;
    let month = (h + m - 7 * n + 114) / 31;
    let day = ((h + m - 7 * n + 114) % 31) + 1;
    ymd(year, month as u32, day as u32)
}

/// Sat → prior Fri, Sun → following Mon.
fn observed_fixed(d: NaiveDate) -> NaiveDate {
    match d.weekday() {
        Weekday::Sat => d - chrono::Duration::days(1),
        Weekday::Sun => d + chrono::Duration::days(1),
        _ => d,
    }
}

fn nyse_holidays(year: i32) -> Vec<NaiveDate> {
    let mut days = Vec::with_capacity(10);
    let nyd = ymd(year, 1, 1);
    // NYSE does NOT close the preceding Friday when Jan 1 is a Saturday.
    match nyd.weekday() {
        Weekday::Sun => days.push(nyd + chrono::Duration::days(1)),
        Weekday::Sat => {}
        _ => days.push(nyd),
    }
    if year >= MLK_FIRST_YEAR {
        days.push(nth_weekday(year, 1, 0, 3));
    }
    days.push(nth_weekday(year, 2, 0, 3));
    days.push(easter(year) - chrono::Duration::days(2));
    days.push(last_weekday(year, 5, 0));
    if year >= JUNETEENTH_FIRST_YEAR {
        days.push(observed_fixed(ymd(year, 6, 19)));
    }
    days.push(observed_fixed(ymd(year, 7, 4)));
    days.push(nth_weekday(year, 9, 0, 1));
    days.push(nth_weekday(year, 11, 3, 4));
    days.push(observed_fixed(ymd(year, 12, 25)));
    days
}

pub fn is_trading_day(d: NaiveDate) -> bool {
    if matches!(d.weekday(), Weekday::Sat | Weekday::Sun) {
        return false;
    }
    if AD_HOC_CLOSURES.iter().any(|&(y, m, dd)| ymd(y, m, dd) == d) {
        return false;
    }
    !nyse_holidays(d.year()).contains(&d)
}

/// NYSE half-day (13:00 ET close): July 3 when it trades, the day after
/// Thanksgiving, Christmas Eve Mon–Thu.
pub fn is_early_close(d: NaiveDate) -> bool {
    if !is_trading_day(d) {
        return false;
    }
    if d.month() == 7 && d.day() == 3 {
        return true;
    }
    if d == nth_weekday(d.year(), 11, 3, 4) + chrono::Duration::days(1) {
        return true;
    }
    d.month() == 12 && d.day() == 24
}

/// `is_market_open`: a trading day, 09:30 ET ≤ now < the day's close (16:00 / 13:00).
pub fn is_market_open(now_et: &chrono::DateTime<chrono_tz::Tz>) -> bool {
    let d = now_et.date_naive();
    if !is_trading_day(d) {
        return false;
    }
    let close_min = if is_early_close(d) { 13 * 60 } else { 16 * 60 };
    let now_min = now_et.hour() as i64 * 60 + now_et.minute() as i64;
    (9 * 60 + 30) <= now_min && now_min < close_min
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn holidays_2026() {
        // Cross-checked against trading/market_calendar.py.
        for (m, d) in [
            (1, 1),
            (1, 19),
            (2, 16),
            (4, 3),
            (5, 25),
            (6, 19),
            (7, 3),
            (9, 7),
            (11, 26),
            (12, 25),
        ] {
            assert!(!is_trading_day(ymd(2026, m, d)), "{m}/{d}");
        }
        assert!(is_trading_day(ymd(2026, 8, 17)));
        assert!(!is_trading_day(ymd(2026, 8, 15))); // Saturday
        assert!(!is_trading_day(ymd(2025, 1, 9))); // ad hoc
    }

    #[test]
    fn early_closes() {
        assert!(is_early_close(ymd(2026, 11, 27)));
        assert!(is_early_close(ymd(2026, 12, 24)));
        assert!(!is_early_close(ymd(2026, 7, 3))); // observed holiday (July 4 = Saturday)
        assert!(is_early_close(ymd(2025, 7, 3)));
        assert!(!is_early_close(ymd(2026, 8, 17)));
    }

    #[test]
    fn easter_dates() {
        assert_eq!(easter(2026), ymd(2026, 4, 5));
        assert_eq!(easter(2025), ymd(2025, 4, 20));
    }
}
