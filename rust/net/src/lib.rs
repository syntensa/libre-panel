//! A file from the web, as the Python app's urllib.request.urlopen fetches one: http and
//! https, the certificates the system trusts, redirects followed, the proxy the environment
//! names, an answer of 400 and above an error.
//!
//! Nothing is sent but the request for the address: no cookies are kept and no body goes out.

use std::io::Read;
use std::time::Duration;

use ureq::tls::{RootCerts, TlsConfig};
use ureq::Agent;

/// Why an address gave nothing.
#[derive(Clone, Debug, PartialEq)]
pub struct Error {
    /// the answer's status, where the server answered with an error (HTTPError's code)
    pub status: Option<u16>,
    pub text: String,
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.text)
    }
}

impl std::error::Error for Error {}

fn agent(timeout: f64) -> Agent {
    let wait = Some(Duration::from_secs_f64(timeout));
    // (urlopen's timeout is that of each step: connecting, each write, each read)
    Agent::config_builder()
        .timeout_resolve(wait)
        .timeout_connect(wait)
        .timeout_send_request(wait)
        .timeout_recv_response(wait)
        .timeout_per_read(wait)
        .timeout_per_write(wait)
        .tls_config(
            TlsConfig::builder()
                .root_certs(RootCerts::PlatformVerifier)
                .build(),
        )
        .build()
        .new_agent()
}

/// The first `limit` bytes of what `url` answers to a GET with `headers`; each step of it may
/// take `timeout` seconds.
pub fn get(
    url: &str,
    headers: &[(&str, &str)],
    timeout: f64,
    limit: usize,
) -> Result<Vec<u8>, Error> {
    let mut request = agent(timeout).get(url);
    for (name, value) in headers {
        request = request.header(*name, *value);
    }
    let answer = request.call().map_err(|why| Error {
        status: match &why {
            ureq::Error::StatusCode(code) => Some(*code),
            _ => None,
        },
        text: why.to_string(),
    })?;
    let mut data = Vec::new();
    answer
        .into_body()
        .into_reader()
        .take(limit as u64)
        .read_to_end(&mut data)
        .map_err(|why| Error {
            status: None,
            text: why.to_string(),
        })?;
    Ok(data)
}

pub mod stream;

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;

    use super::get;

    /// A web server of this computer for one test: answers each request by its path.
    fn serve() -> String {
        let listener = TcpListener::bind("127.0.0.1:0").expect("a port of this computer");
        let address = listener.local_addr().expect("its address");
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else {
                    return;
                };
                let mut asked = Vec::new();
                let mut buffer = [0u8; 1024];
                while !asked.windows(4).any(|end| end == b"\r\n\r\n") {
                    match stream.read(&mut buffer) {
                        Ok(count) if count > 0 => asked.extend(&buffer[..count]),
                        _ => break,
                    }
                }
                let asked = String::from_utf8_lossy(&asked).into_owned();
                let path = asked.split(' ').nth(1).unwrap_or("");
                let agent = asked
                    .lines()
                    .find_map(|line| line.strip_prefix("user-agent: "))
                    .or_else(|| {
                        asked
                            .lines()
                            .find_map(|line| line.strip_prefix("User-Agent: "))
                    })
                    .unwrap_or("")
                    .to_string();
                let answer = match path {
                    "/cover" => {
                        let body = "0123456789".repeat(100);
                        format!(
                            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{body}",
                            body.len()
                        )
                    }
                    "/agent" => {
                        format!(
                            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{agent}",
                            agent.len()
                        )
                    }
                    "/moved" => {
                        "HTTP/1.1 302 Found\r\nLocation: /cover\r\nContent-Length: 0\r\n\r\n"
                            .to_string()
                    }
                    _ => "HTTP/1.1 404 Not Found\r\nContent-Length: 4\r\n\r\ngone".to_string(),
                };
                let _ = stream.write_all(answer.as_bytes());
            }
        });
        format!("http://{address}")
    }

    #[test]
    fn a_file_is_fetched_whole_or_up_to_the_limit() {
        // (the proxy of the environment is not for this computer's own server)
        std::env::set_var("NO_PROXY", "127.0.0.1");
        let server = serve();
        let whole = get(&format!("{server}/cover"), &[], 5.0, 4096).expect("the file");
        assert_eq!(whole, "0123456789".repeat(100).into_bytes());
        let part = get(&format!("{server}/cover"), &[], 5.0, 25).expect("its start");
        assert_eq!(part, b"0123456789012345678901234");
        let moved = get(&format!("{server}/moved"), &[], 5.0, 4096).expect("where it moved to");
        assert_eq!(moved, whole);
        let named = get(
            &format!("{server}/agent"),
            &[("User-Agent", "libre-panel")],
            5.0,
            99,
        );
        assert_eq!(named.expect("the name"), b"libre-panel");
    }

    /// (asks the internet: run by hand, `cargo test -p lp-net -- --ignored`)
    #[test]
    #[ignore]
    fn an_https_address_is_fetched_with_the_systems_certificates() {
        let page = get(
            "https://crates.io/robots.txt",
            &[("User-Agent", "libre-panel")],
            10.0,
            64,
        );
        assert!(!page.expect("the page").is_empty());
    }

    #[test]
    fn an_answer_of_an_error_is_one_with_its_status() {
        std::env::set_var("NO_PROXY", "127.0.0.1");
        let server = serve();
        let gone = get(&format!("{server}/nothing"), &[], 5.0, 4096).expect_err("an error");
        assert_eq!(gone.status, Some(404));
        let none = get("http://127.0.0.1:1/x", &[], 2.0, 4096).expect_err("nobody listens");
        assert_eq!(none.status, None);
        assert!(get("nonsense", &[], 2.0, 10).is_err());
    }
}
