//! A connection to a server's port, in the open or through TLS with the certificates the
//! system trusts (Python's ssl.create_default_context): what a protocol of its own (MQTT)
//! talks over.

use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::sync::Arc;
use std::time::Duration;

use rustls::pki_types::ServerName;
use rustls::{ClientConfig, ClientConnection, StreamOwned};
use rustls_platform_verifier::BuilderVerifierExt;

use crate::Error;

/// A connection: bytes out, bytes back.
pub enum Stream {
    Plain(TcpStream),
    Tls(Box<StreamOwned<ClientConnection, TcpStream>>),
}

fn failed(why: impl std::fmt::Display) -> Error {
    Error {
        status: None,
        text: why.to_string(),
    }
}

impl Stream {
    /// Connects to `host`'s `port` (each of its addresses in turn, each within `timeout`
    /// seconds), with TLS where `tls` says so.
    pub fn connect(host: &str, port: u16, tls: bool, timeout: f64) -> Result<Stream, Error> {
        let wait = Duration::from_secs_f64(timeout);
        let mut last = failed(format!("no address of {host}"));
        let mut socket = None;
        for address in (host, port).to_socket_addrs().map_err(failed)? {
            match TcpStream::connect_timeout(&address, wait) {
                Ok(found) => {
                    socket = Some(found);
                    break;
                }
                Err(why) => last = failed(why),
            }
        }
        let socket = socket.ok_or(last)?;
        socket.set_nodelay(true).map_err(failed)?;
        socket.set_read_timeout(Some(wait)).map_err(failed)?;
        socket.set_write_timeout(Some(wait)).map_err(failed)?;
        if !tls {
            return Ok(Stream::Plain(socket));
        }
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let config = ClientConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()
            .map_err(failed)?
            .with_platform_verifier()
            .map_err(failed)?
            .with_no_client_auth();
        let name = ServerName::try_from(host.to_string()).map_err(failed)?;
        let session = ClientConnection::new(Arc::new(config), name).map_err(failed)?;
        Ok(Stream::Tls(Box::new(StreamOwned::new(session, socket))))
    }

    fn socket(&self) -> &TcpStream {
        match self {
            Stream::Plain(socket) => socket,
            Stream::Tls(stream) => &stream.sock,
        }
    }

    /// How long a read waits for bytes before it gives up (an error of the kind TimedOut or
    /// WouldBlock; what was read before is kept).
    pub fn set_read_timeout(&self, seconds: f64) -> std::io::Result<()> {
        let wait = Duration::from_secs_f64(seconds);
        self.socket().set_read_timeout(Some(wait))
    }

    /// Ends the connection, both ways.
    pub fn close(&mut self) {
        if let Stream::Tls(stream) = self {
            stream.conn.send_close_notify();
            let _ = stream.flush();
        }
        let _ = self.socket().shutdown(std::net::Shutdown::Both);
    }
}

impl Read for Stream {
    fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
        match self {
            Stream::Plain(socket) => socket.read(buffer),
            Stream::Tls(stream) => stream.read(buffer),
        }
    }
}

impl Write for Stream {
    fn write(&mut self, data: &[u8]) -> std::io::Result<usize> {
        match self {
            Stream::Plain(socket) => socket.write(data),
            Stream::Tls(stream) => stream.write(data),
        }
    }

    fn flush(&mut self) -> std::io::Result<()> {
        match self {
            Stream::Plain(socket) => socket.flush(),
            Stream::Tls(stream) => stream.flush(),
        }
    }
}
