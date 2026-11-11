//! HTTP as Python's http.server speaks it (BaseHTTPRequestHandler, HTTP/1.0: one request a
//! connection): the request's line and headers read with its limits, its errors answered in
//! its words, and the answers of the app's server (editor/server.py's _send, _json, _error,
//! _redirect and the body it reads or discards).

use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;

use lp_py::{Dict, Value};

/// the longest line of a request (http.client's _MAXLINE)
const MAX_LINE: usize = 65536;
/// the most headers (http.client's _MAXHEADERS)
const MAX_HEADERS: usize = 100;
/// a body that is not asked for is read and thrown away up to this size (MAX_ASSET)
const MAX_DISCARDED: u64 = 10 * 1024 * 1024;

const CONTENT_SECURITY: &str =
    "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'";

/// http.HTTPStatus: a status's phrase and what it means (for an error's page).
pub fn status_words(status: u16) -> (&'static str, &'static str) {
    match status {
        200 => ("OK", "Request fulfilled, document follows"),
        204 => ("No Content", "Request fulfilled, nothing follows"),
        301 => ("Moved Permanently", "Object moved permanently -- see URI list"),
        400 => ("Bad Request", "Bad request syntax or unsupported method"),
        403 => ("Forbidden", "Request forbidden -- authorization will not help"),
        404 => ("Not Found", "Nothing matches the given URI"),
        409 => ("Conflict", "Request conflict"),
        413 => ("Request Entity Too Large", "Entity is too large"),
        414 => ("Request-URI Too Long", "URI is too long"),
        415 => ("Unsupported Media Type", "Entity body in unsupported format"),
        431 => (
            "Request Header Fields Too Large",
            "The server is unwilling to process the request because its header fields are too large",
        ),
        500 => ("Internal Server Error", "Server got itself in trouble"),
        501 => ("Not Implemented", "Server does not support this operation"),
        502 => ("Bad Gateway", "Invalid responses from another server/proxy"),
        503 => (
            "Service Unavailable",
            "The server cannot process the request due to a high load",
        ),
        505 => ("HTTP Version Not Supported", "Cannot fulfill request"),
        _ => ("", ""),
    }
}

/// email.utils.formatdate(usegmt=True) of now: `Tue, 10 Nov 2026 17:00:00 GMT`.
pub fn http_date(since_epoch: u64) -> String {
    const DAYS: [&str; 7] = ["Thu", "Fri", "Sat", "Sun", "Mon", "Tue", "Wed"];
    const MONTHS: [&str; 12] = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ];
    let (days, rest) = (since_epoch / 86400, since_epoch % 86400);
    // (the civil date of a day's number: the era's 400 years, from 1 March)
    let shifted = days as i64 + 719_468;
    let era = shifted.div_euclid(146_097);
    let of_era = shifted.rem_euclid(146_097);
    let year_of_era = (of_era - of_era / 1460 + of_era / 36524 - of_era / 146_096) / 365;
    let of_year = of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let shifted_month = (5 * of_year + 2) / 153;
    let day = of_year - (153 * shifted_month + 2) / 5 + 1;
    let month = (shifted_month + 2) % 12 + 1;
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    format!(
        "{}, {day:02} {} {year:04} {:02}:{:02}:{:02} GMT",
        DAYS[(days % 7) as usize],
        MONTHS[(month - 1) as usize],
        rest / 3600,
        rest % 3600 / 60,
        rest % 60
    )
}

fn now() -> String {
    let since = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH);
    http_date(since.map_or(0, |since| since.as_secs()))
}

/// html.escape(text, quote=False)
fn escaped(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

/// A request's address in its parts (urllib.parse.urlparse, as far as a request's line can
/// hold one).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Url {
    pub path: String,
    pub query: String,
}

pub fn urlparse(target: &str) -> Url {
    let strip = |c: char| c <= ' ';
    let mut rest: String = target.trim_start_matches(strip).to_string();
    rest.retain(|c| !matches!(c, '\t' | '\r' | '\n'));
    // (a scheme: letters, digits, "+", "-" and "." before a colon, a letter first)
    if let Some(colon) = rest.find(':') {
        let scheme = &rest[..colon];
        let letters = |c: char| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.');
        if scheme.starts_with(|c: char| c.is_ascii_alphabetic()) && scheme.chars().all(letters) {
            rest = rest[colon + 1..].to_string();
        }
    }
    if let Some(after) = rest.strip_prefix("//") {
        let end = after.find(['/', '?', '#']).unwrap_or(after.len());
        rest = after[end..].to_string();
    }
    let rest = rest
        .split_once('#')
        .map_or(rest.as_str(), |(before, _)| before);
    let (path, query) = rest.split_once('?').unwrap_or((rest, ""));
    Url {
        path: path.to_string(),
        query: query.to_string(),
    }
}

/// urllib.parse.unquote: `%41` is the byte 0x41; the bytes read as UTF-8, a wrong one as
/// U+FFFD.
pub fn unquote(text: &str) -> String {
    if !text.contains('%') {
        return text.to_string();
    }
    let bytes = text.as_bytes();
    let (mut out, mut at) = (Vec::with_capacity(bytes.len()), 0);
    while at < bytes.len() {
        let hex = |at: usize| bytes.get(at).and_then(|b| char::from(*b).to_digit(16));
        match (bytes[at], hex(at + 1), hex(at + 2)) {
            (b'%', Some(high), Some(low)) => {
                out.push((high * 16 + low) as u8);
                at += 3;
            }
            (byte, _, _) => {
                out.push(byte);
                at += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// urllib.parse.parse_qs: the values of each name in a query; a pair without `=` or without
/// a value is left out.
pub fn parse_qs(query: &str) -> indexmap::IndexMap<String, Vec<String>> {
    let mut found: indexmap::IndexMap<String, Vec<String>> = indexmap::IndexMap::new();
    for pair in query.split('&') {
        let Some((name, value)) = pair.split_once('=') else {
            continue;
        };
        if value.is_empty() {
            continue;
        }
        let plain = |text: &str| unquote(&text.replace('+', " "));
        found.entry(plain(name)).or_default().push(plain(value));
    }
    found
}

/// parse_qs(query, keep_blank_values=True): a name without a value, or without "=", too.
pub fn parse_qs_blank(query: &str) -> indexmap::IndexMap<String, Vec<String>> {
    let mut found: indexmap::IndexMap<String, Vec<String>> = indexmap::IndexMap::new();
    for pair in query.split('&') {
        if pair.is_empty() {
            continue;
        }
        let (name, value) = pair.split_once('=').unwrap_or((pair, ""));
        let plain = |text: &str| unquote(&text.replace('+', " "));
        found.entry(plain(name)).or_default().push(plain(value));
    }
    found
}

/// The first value of a name in a query, or "".
pub fn first<'a>(query: &'a indexmap::IndexMap<String, Vec<String>>, name: &str) -> &'a str {
    query
        .get(name)
        .and_then(|values| values.first())
        .map_or("", String::as_str)
}

/// A request and the way to answer it.
pub struct Exchange {
    pub method: String,
    /// the request's address as it came (after `//` at its start was made `/`)
    pub target: String,
    /// "HTTP/0.9" for a request of two words: its answer has no headers
    version: String,
    headers: Vec<(String, String)>,
    reader: BufReader<TcpStream>,
    writer: TcpStream,
    body_read: bool,
    /// `Server`'s value
    software: String,
    /// the status answered (None: nothing yet)
    pub answered: Option<u16>,
}

/// A line of at most `limit` bytes (readline(limit)): with its line break, where it has one.
fn read_line(reader: &mut BufReader<TcpStream>, limit: usize) -> Vec<u8> {
    let mut line = Vec::new();
    let _ = reader.take(limit as u64).read_until(b'\n', &mut line);
    line
}

/// str(bytes, 'iso-8859-1')
fn latin(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| char::from(*byte)).collect()
}

impl Exchange {
    /// _client_gone: whether the client closed this request's connection (a page it left, a
    /// picture it no longer needs): then there is nobody to answer. A client that is still
    /// there has sent nothing more.
    pub fn client_gone(&self) -> bool {
        if self.writer.set_nonblocking(true).is_err() {
            return true;
        }
        let mut byte = [0u8; 1];
        let gone = match self.writer.peek(&mut byte) {
            Ok(n) => n == 0,
            Err(e) => e.kind() != std::io::ErrorKind::WouldBlock,
        };
        gone || self.writer.set_nonblocking(false).is_err()
    }

    /// handle_one_request up to the method: the request read; None where it was answered
    /// already (an error of http.server's own) or there is none.
    pub fn read(stream: TcpStream, software: &str) -> Option<Exchange> {
        let writer = stream.try_clone().ok()?;
        let mut exchange = Exchange {
            method: String::new(),
            target: String::new(),
            version: "HTTP/0.9".into(),
            headers: Vec::new(),
            reader: BufReader::new(stream),
            writer,
            body_read: false,
            software: software.to_string(),
            answered: None,
        };
        let raw = read_line(&mut exchange.reader, MAX_LINE + 1);
        if raw.len() > MAX_LINE {
            exchange.version.clear();
            exchange.send_error(414, None, None);
            return None;
        }
        if raw.is_empty() {
            return None;
        }
        let line = latin(&raw);
        let line = line.trim_end_matches(['\r', '\n']);
        let words: Vec<&str> = line
            .split(lp_py::isspace)
            .filter(|w| !w.is_empty())
            .collect();
        if words.is_empty() {
            return None;
        }
        if words.len() >= 3 {
            let version = words[words.len() - 1];
            let numbers = version.strip_prefix("HTTP/").and_then(|base| {
                let parts: Vec<&str> = base.split('.').collect();
                let digits = |part: &&str| {
                    !part.is_empty()
                        && part.chars().all(lp_py::isdigit)
                        && part.chars().count() <= 10
                };
                let whole = |part: &str| lp_py::int_of_str(part)?.to_string().parse::<u64>().ok();
                match parts.as_slice() {
                    [major, minor] if digits(major) && digits(minor) => {
                        Some((base, whole(major)?, whole(minor)?))
                    }
                    _ => None,
                }
            });
            let Some((base, major, _)) = numbers else {
                let said = format!("Bad request version ({})", lp_py::repr(version));
                exchange.send_error(400, Some(&said), None);
                return None;
            };
            if major >= 2 {
                let said = format!("Invalid HTTP version ({base})");
                exchange.send_error(505, Some(&said), None);
                return None;
            }
            exchange.version = version.to_string();
        }
        if !(2..=3).contains(&words.len()) {
            let said = format!("Bad request syntax ({})", lp_py::repr(line));
            exchange.send_error(400, Some(&said), None);
            return None;
        }
        if words.len() == 2 && words[0] != "GET" {
            let said = format!("Bad HTTP/0.9 request type ({})", lp_py::repr(words[0]));
            exchange.send_error(400, Some(&said), None);
            return None;
        }
        exchange.method = words[0].to_string();
        exchange.target = match words[1].starts_with("//") {
            // (reduced to a single /)
            true => format!("/{}", words[1].trim_start_matches('/')),
            false => words[1].to_string(),
        };
        // the headers: every line up to an empty one, then read as a message's
        let mut lines: Vec<String> = Vec::new();
        loop {
            let raw = read_line(&mut exchange.reader, MAX_LINE + 1);
            if raw.len() > MAX_LINE {
                let why = format!("got more than {MAX_LINE} bytes when reading header line");
                exchange.send_error(431, Some("Line too long"), Some(&why));
                return None;
            }
            if raw.is_empty() || raw == b"\r\n" || raw == b"\n" {
                break;
            }
            lines.push(latin(&raw));
            if lines.len() > MAX_HEADERS {
                let why = format!("got more than {MAX_HEADERS} headers");
                exchange.send_error(431, Some("Too many headers"), Some(&why));
                return None;
            }
        }
        for line in lines {
            let line = line.trim_end_matches(['\r', '\n']);
            if line.starts_with([' ', '\t']) {
                // (a header's value goes on)
                if let Some((_, value)) = exchange.headers.last_mut() {
                    value.push_str(line);
                }
                continue;
            }
            let Some((name, value)) = line.split_once(':') else {
                break; // (no header: what follows is none either)
            };
            let value = value.trim_start_matches([' ', '\t']);
            exchange.headers.push((name.to_string(), value.to_string()));
        }
        Some(exchange)
    }

    /// headers.get(name): the first header of that name, whatever its letters' case.
    pub fn header(&self, name: &str) -> Option<&str> {
        let found = self
            .headers
            .iter()
            .find(|(key, _)| key.eq_ignore_ascii_case(name));
        found.map(|(_, value)| value.as_str())
    }

    /// The status line and the headers (none for a request without a version), then the
    /// body.
    fn respond(&mut self, status: u16, phrase: &str, headers: &[(&str, &str)], body: &[u8]) {
        self.answered = Some(status);
        let mut out = Vec::new();
        if self.version != "HTTP/0.9" {
            out.extend_from_slice(format!("HTTP/1.0 {status} {phrase}\r\n").as_bytes());
            let date = now();
            let first = [("Server", self.software.as_str()), ("Date", date.as_str())];
            for (name, value) in first.iter().chain(headers) {
                // (headers are Latin-1 on the wire)
                let line = format!("{name}: {value}\r\n");
                out.extend(
                    line.chars()
                        .map(|c| u8::try_from(u32::from(c)).unwrap_or(b'?')),
                );
            }
            out.extend_from_slice(b"\r\n");
        }
        out.extend_from_slice(body);
        let _ = self.writer.write_all(&out);
        let _ = self.writer.flush();
    }

    /// send_error: an error of http.server's own, with its page.
    pub fn send_error(&mut self, status: u16, message: Option<&str>, explain: Option<&str>) {
        let (short, long) = status_words(status);
        let (message, explain) = (message.unwrap_or(short), explain.unwrap_or(long));
        let body = format!(
            "<!DOCTYPE HTML>\n<html lang=\"en\">\n    <head>\n        <meta charset=\"utf-8\">\n        \
             <title>Error response</title>\n    </head>\n    <body>\n        <h1>Error response</h1>\n        \
             <p>Error code: {status}</p>\n        <p>Message: {}.</p>\n        \
             <p>Error code explanation: {status} - {}.</p>\n    </body>\n</html>\n",
            escaped(message),
            escaped(explain)
        );
        let length = body.len().to_string();
        let headers = [
            ("Connection", "close"),
            ("Content-Type", "text/html;charset=utf-8"),
            ("Content-Length", length.as_str()),
        ];
        let body = match self.method == "HEAD" {
            true => &[][..],
            false => body.as_bytes(),
        };
        self.respond(status, message, &headers, body);
    }

    /// _send: an answer of the app's server. `filename`: a file to keep.
    pub fn send(&mut self, status: u16, body: &[u8], content_type: &str, filename: &str) {
        let length = body.len().to_string();
        let keep = format!("attachment; filename=\"{filename}\"");
        let mut headers = vec![("Content-Type", content_type)];
        if !filename.is_empty() {
            headers.push(("Content-Disposition", keep.as_str()));
        }
        headers.extend([
            ("Content-Length", length.as_str()),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            ("Content-Security-Policy", CONTENT_SECURITY),
        ]);
        self.respond(status, status_words(status).0, &headers, body);
    }

    /// _json
    pub fn json(&mut self, data: &Value, status: u16) {
        let text = lp_py::json::dumps(data, &lp_py::json::Dumps::default());
        self.send(status, text.as_bytes(), "application/json", "");
    }

    /// _error: an error answer; `reason` names a kind the app tells by itself ("token": the
    /// page is from an earlier run of Libre Panel).
    pub fn error(&mut self, message: &str, status: u16, reason: &str) {
        self.discard_body();
        let mut told = Dict::new();
        told.insert("error".into(), Value::Str(message.to_string()));
        if !reason.is_empty() {
            told.insert("reason".into(), Value::Str(reason.to_string()));
        }
        self.json(&Value::Dict(told), status);
    }

    /// _redirect
    pub fn redirect(&mut self, location: &str) {
        let headers = [
            ("Location", location),
            ("Content-Length", "0"),
            ("Cache-Control", "no-store"),
        ];
        self.respond(301, status_words(301).0, &headers, b"");
    }

    /// int(headers.get("Content-Length", "0")): None where it is no number.
    fn content_length(&self) -> Option<i128> {
        let given = self.header("Content-Length").unwrap_or("0");
        lp_py::int_of_str(given)?.to_string().parse().ok()
    }

    fn take(&mut self, length: u64) -> Vec<u8> {
        let mut body = Vec::new();
        let _ = (&mut self.reader).take(length).read_to_end(&mut body);
        body
    }

    /// _discard_body: read what the client sent before answering without it. Closing a
    /// socket with unread data resets the connection (at once on Windows), and the client
    /// then sees a connection error instead of the answer.
    pub fn discard_body(&mut self) {
        if self.body_read {
            return;
        }
        self.body_read = true;
        if let Some(length) = self.content_length() {
            if length > 0 && length <= i128::from(MAX_DISCARDED) {
                self.take(length as u64);
            }
        }
    }

    /// int(headers.get("Content-Length", "-1")): -1 where it is none or no number.
    pub fn length_given(&self) -> i128 {
        self.header("Content-Length")
            .and_then(lp_py::int_of_str)
            .and_then(|n| n.to_string().parse().ok())
            .unwrap_or(-1)
    }

    /// The body, `length` bytes of it, written to `out` as it comes (a file of gigabytes
    /// too); an error where it ends before.
    pub fn copy_body(&mut self, length: u64, out: &mut dyn std::io::Write) -> std::io::Result<()> {
        self.body_read = true;
        let mut left = length;
        let mut buffer = vec![0u8; 1 << 20];
        while left > 0 {
            let want = left.min(buffer.len() as u64) as usize;
            let got = self.reader.read(&mut buffer[..want])?;
            if got == 0 {
                return Err(std::io::Error::other(
                    "the upload ended before the file did",
                ));
            }
            out.write_all(&buffer[..got])?;
            left -= got as u64;
        }
        Ok(())
    }

    /// _read_body: the request's body; None (and the error answered) where it is larger than
    /// `limit`.
    pub fn read_body(&mut self, limit: u64, too_large: &str) -> Option<Vec<u8>> {
        let length = self.content_length().unwrap_or(-1);
        if length < 0 || length > i128::from(limit) {
            self.error(too_large, 413, "");
            return None;
        }
        self.body_read = true;
        Some(self.take(length as u64))
    }

    /// _read_json: the request's body as JSON; None (and the error answered) where it is
    /// none, None too (and nothing answered) where it says null.
    pub fn read_json(&mut self, limit: u64) -> Option<Value> {
        let body = self.read_body(limit, "request too large")?;
        let read = (String::from_utf8(body).ok()).and_then(|text| lp_py::json::loads(&text).ok());
        if read.is_none() {
            self.error("invalid JSON", 400, "");
        }
        // (a body that says null is None to Python's routes as a body that is none is: they
        // answer nothing)
        read.filter(|value| !matches!(value, Value::None))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dates_and_addresses_read_as_pythons() {
        assert_eq!(http_date(0), "Thu, 01 Jan 1970 00:00:00 GMT");
        assert_eq!(http_date(1_794_330_061), "Tue, 10 Nov 2026 17:01:01 GMT");
        assert_eq!(http_date(951_782_400), "Tue, 29 Feb 2000 00:00:00 GMT");
        let url = |path: &str, query: &str| Url {
            path: path.into(),
            query: query.into(),
        };
        assert_eq!(urlparse("/api/x?a=1&b#frag"), url("/api/x", "a=1&b"));
        assert_eq!(urlparse("http://host:1/p/q?x"), url("/p/q", "x"));
        assert_eq!(urlparse("/a:b"), url("/a:b", ""));
        assert_eq!(urlparse("*"), url("*", ""));
        let query = parse_qs("a=1&a=2&b=&c&d=x+y%20z%C3%A4%ff%4");
        assert_eq!(query["a"], ["1", "2"]);
        assert_eq!(query.len(), 2);
        assert_eq!(first(&query, "d"), "x y zä\u{fffd}%4");
        assert_eq!(first(&query, "b"), "");
    }
}
