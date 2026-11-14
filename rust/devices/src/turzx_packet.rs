//! The TURZX command packet (devices/turzx_usb.py's build_packet and UsbTransport), which Lian
//! Li's USB screens speak too: one 512-byte packet a command, 500 bytes of plain text ([0] the
//! command, [2..4] the magic 1A 6D, [4..8] milliseconds since local midnight, little endian,
//! [8..] the arguments) DES-CBC encrypted with key = IV = "slv3tuzx" (zero padded to 504), bytes
//! 510 and 511 the trailer A1 1A; a payload follows the packet in the same transfer. (The TURZX
//! panels themselves are driven by Libre Panel's engine, which builds the same packets.)

use cbc::cipher::{BlockEncryptMut, KeyIvInit};

use crate::base::{DeviceError, Result};
use crate::usb_link::{UsbError, UsbIo};

pub const KEY: &[u8; 8] = b"slv3tuzx";
pub const MAGIC: [u8; 2] = [0x1a, 0x6d];
pub const TRAILER: [u8; 2] = [0xa1, 0x1a];
pub const PACKET: usize = 512;
pub const MAX_ARGS: usize = 492;
/// the most a reply is read as
pub const READ_LEN: usize = 1024;
/// the mark of a command accepted
pub const ACK: u8 = 0xC8;

type Encryptor = cbc::Encryptor<des::Des>;

/// ms_since_midnight: the milliseconds since local midnight at `now` (seconds since 1970), from
/// the local wall clock.
pub fn ms_since_midnight(now: f64) -> u32 {
    let utc = lp_py::zones::Zone::utc();
    let zone = lp_py::zones::local().unwrap_or(&utc);
    match lp_py::zones::fromtimestamp(zone, now) {
        Some((local, _)) => {
            let seconds = (u64::from(local.hour) * 60 + u64::from(local.minute)) * 60
                + u64::from(local.second);
            ((seconds * 1000 + u64::from(local.microsecond / 1000)) & 0xFFFF_FFFF) as u32
        }
        None => 0,
    }
}

/// build_packet: the packet of command `cmd` with its `args`, stamped `ms`.
pub fn packet(cmd: u8, args: &[u8], ms: u32) -> Result<[u8; PACKET]> {
    if args.len() > MAX_ARGS {
        return Err(DeviceError("arguments too long".into()));
    }
    let mut plain = [0u8; 504];
    plain[0] = cmd;
    plain[2..4].copy_from_slice(&MAGIC);
    plain[4..8].copy_from_slice(&ms.to_le_bytes());
    plain[8..8 + args.len()].copy_from_slice(args);
    let mut cipher = Encryptor::new(KEY.into(), KEY.into());
    for block in plain.chunks_exact_mut(8) {
        cipher.encrypt_block_mut(block.into());
    }
    let mut out = [0u8; PACKET];
    out[..504].copy_from_slice(&plain);
    out[510..].copy_from_slice(&TRAILER);
    Ok(out)
}

fn hex16(reply: &[u8]) -> String {
    reply
        .iter()
        .take(16)
        .map(|b| format!("{b:02x}"))
        .collect::<Vec<_>>()
        .join(" ")
}

/// A clock: seconds since 1970 (the tests' a fixed one).
pub type Clock = fn() -> f64;

/// What a packet is stamped with: the milliseconds since local midnight now (the tests' a
/// fixed number).
pub type Stamp = fn() -> u32;

pub fn now_stamp() -> u32 {
    ms_since_midnight(wall_clock())
}

pub fn wall_clock() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |d| d.as_secs_f64())
}

/// UsbTransport: the commands on interface 0's bulk endpoints, each with its reply.
pub struct Transport {
    pub io: Box<dyn UsbIo>,
    pub out: u8,
    pub into: u8,
    /// replies to queries no longer waited for; skipped when they come
    late: u32,
    pub stamp: Stamp,
}

impl Transport {
    pub fn new(io: Box<dyn UsbIo>, out: u8, into: u8, stamp: Stamp) -> Transport {
        Transport {
            io,
            out,
            into,
            late: 0,
            stamp,
        }
    }

    fn build(&self, cmd: u8, args: &[u8]) -> Result<[u8; PACKET]> {
        packet(cmd, args, (self.stamp)())
    }

    fn usb_error(cmd: u8, e: &UsbError) -> DeviceError {
        DeviceError(format!("USB error on command {cmd}: {e}"))
    }

    /// drain: replies left over from an earlier session, thrown away.
    pub fn drain(&mut self, timeout_ms: u64, limit: usize) -> usize {
        let mut dropped = 0;
        for _ in 0..limit {
            if self.io.read(self.into, READ_LEN, timeout_ms).is_err() {
                break;
            }
            dropped += 1;
        }
        self.late = 0;
        dropped
    }

    fn reply(&mut self, cmd: u8, timeout_ms: u64) -> std::result::Result<Vec<u8>, UsbError> {
        let mut reply = self.io.read(self.into, READ_LEN, timeout_ms)?;
        while self.late > 0 && reply.first() != Some(&cmd) {
            self.late -= 1;
            reply = self.io.read(self.into, READ_LEN, timeout_ms)?;
        }
        Ok(reply)
    }

    /// ask: a command whose reply carries data; with `echo` it must begin with the command.
    pub fn ask(&mut self, cmd: u8, args: &[u8], timeout_ms: u64, echo: bool) -> Result<Vec<u8>> {
        let packet = self.build(cmd, args)?;
        self.io
            .write(self.out, &packet, timeout_ms)
            .map_err(|e| Self::usb_error(cmd, &e))?;
        let reply = self
            .reply(cmd, timeout_ms)
            .map_err(|e| Self::usb_error(cmd, &e))?;
        if echo && reply.first() != Some(&cmd) {
            return Err(DeviceError(format!(
                "command {cmd}: unexpected reply {}",
                hex16(&reply)
            )));
        }
        Ok(reply)
    }

    /// push: a command and its payload in one write, whose reply may not come: the reply, or
    /// None.
    pub fn push(
        &mut self,
        cmd: u8,
        args: &[u8],
        payload: &[u8],
        timeout_ms: u64,
        reply_ms: u64,
    ) -> Result<Option<Vec<u8>>> {
        let mut data = self.build(cmd, args)?.to_vec();
        data.extend_from_slice(payload);
        self.io
            .write(self.out, &data, timeout_ms)
            .map_err(|e| Self::usb_error(cmd, &e))?;
        match self.io.read(self.into, READ_LEN, reply_ms) {
            Ok(reply) => Ok(Some(reply)),
            Err(UsbError::Timeout) => Ok(None),
            Err(e) => Err(Self::usb_error(cmd, &e)),
        }
    }

    /// query: a status request; None where the answer is late (skipped when it comes).
    pub fn query(&mut self, cmd: u8, timeout_ms: u64) -> Result<Option<Vec<u8>>> {
        let packet = self.build(cmd, &[])?;
        self.io
            .write(self.out, &packet, timeout_ms)
            .map_err(|e| Self::usb_error(cmd, &e))?;
        let reply = match self.reply(cmd, timeout_ms) {
            Ok(reply) => reply,
            Err(UsbError::Timeout) => {
                self.late = (self.late + 1).min(8);
                return Ok(None);
            }
            Err(e) => return Err(Self::usb_error(cmd, &e)),
        };
        if reply.first() != Some(&cmd) {
            return Err(DeviceError(format!(
                "command {cmd}: unexpected reply {}",
                hex16(&reply)
            )));
        }
        Ok(Some(reply))
    }
}
