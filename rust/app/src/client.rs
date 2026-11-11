//! Asking the running app (cli.py's `quit` and `notify`): one request to its server on this
//! computer, without a proxy, as urllib's opener without ProxyHandler sends it.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::time::Duration;

/// The port of the app's server at `url` ("http://127.0.0.1:8765/").
fn port_of(url: &str) -> Option<u16> {
    let rest = url.split_once("://").map_or(url, |(_, rest)| rest);
    let host = rest.split('/').next()?;
    host.rsplit_once(':')?.1.parse().ok()
}

/// post: `body` (JSON) to `path` of the server at `url`, with `headers`, waiting up to
/// `seconds`: the answer's status and body. Err: why it was not answered.
pub fn post(
    url: &str,
    path: &str,
    headers: &[(&str, &str)],
    body: &[u8],
    seconds: f64,
) -> Result<(u16, Vec<u8>), String> {
    let port = port_of(url).ok_or_else(|| format!("no port in {url}"))?;
    let address = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    let wait = Duration::from_secs_f64(seconds);
    let mut link = TcpStream::connect_timeout(&address, wait).map_err(|e| e.to_string())?;
    link.set_read_timeout(Some(wait))
        .map_err(|e| e.to_string())?;
    link.set_write_timeout(Some(wait))
        .map_err(|e| e.to_string())?;
    let mut head = format!(
        "POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\n\
         Content-Length: {}\r\nConnection: close\r\n",
        body.len()
    );
    for (name, value) in headers {
        head.push_str(&format!("{name}: {value}\r\n"));
    }
    head.push_str("\r\n");
    link.write_all(head.as_bytes()).map_err(|e| e.to_string())?;
    link.write_all(body).map_err(|e| e.to_string())?;
    let mut answer = Vec::new();
    link.read_to_end(&mut answer).map_err(|e| e.to_string())?;
    let end = answer
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or("the answer has no end of its head")?;
    let status = String::from_utf8_lossy(&answer[..end]);
    let code = status
        .split_whitespace()
        .nth(1)
        .and_then(|code| code.parse().ok())
        .ok_or("the answer has no status")?;
    Ok((code, answer[end + 4..].to_vec()))
}

#[cfg(test)]
mod tests {
    use super::port_of;

    #[test]
    fn the_port_is_read_from_the_address() {
        assert_eq!(port_of("http://127.0.0.1:8765/"), Some(8765));
        assert_eq!(port_of("http://127.0.0.1:41000"), Some(41000));
        assert_eq!(port_of("http://127.0.0.1/"), None);
    }
}
