//! A TURZX panel's commands and their replies, as devices/turzx_usb.py's ``UsbTransport``
//! has them: the same packets, the same reads and the same checks, so that the panel sees
//! no difference between Libre Panel's Python and its engine.

use crate::link::{Link, LinkError};
use crate::turzx;

/// Replies are 512 bytes, the packet size, so USB ends them with a zero-length packet; a read
/// of more than one packet takes it along (SPUR II; ``READ_LEN`` there).
pub const READ_LEN: usize = 1024;
pub const ACK: u8 = 0xC8;
/// The most late replies to queries that are skipped (``_late`` there).
const LATE_MOST: u32 = 8;

#[derive(Debug)]
pub enum Error {
    /// The link failed, or the panel answered as it should not: a new connection.
    Device(String),
    /// The panel's video decoder hangs (VideoSession.check_hang), the deepest queue it said.
    Hung(u8),
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Error::Device(why) => write!(f, "{why}"),
            Error::Hung(depth) => write!(f, "the video decoder hangs (queue stuck at {depth})"),
        }
    }
}

/// The start of a reply, as the Python driver writes it into its errors: "1a 6d 00 ...".
pub fn hex16(reply: &[u8]) -> String {
    reply
        .iter()
        .take(16)
        .map(|b| format!("{b:02x}"))
        .collect::<Vec<_>>()
        .join(" ")
}

/// Milliseconds since local midnight, from the wall clock (``ms_since_midnight``).
#[cfg(unix)]
pub fn local_ms() -> u32 {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let seconds = now.as_secs() as libc::time_t;
    // SAFETY: localtime_r only writes the tm it is given.
    let mut tm: libc::tm = unsafe { std::mem::zeroed() };
    let ok = unsafe { !libc::localtime_r(&seconds, &mut tm).is_null() };
    let of_day = if ok {
        (tm.tm_hour as u32 * 60 + tm.tm_min as u32) * 60 + tm.tm_sec as u32
    } else {
        (now.as_secs() % 86_400) as u32
    };
    of_day * 1000 + now.subsec_millis()
}

#[cfg(windows)]
pub fn local_ms() -> u32 {
    use windows_sys::Win32::System::SystemInformation::GetLocalTime;
    // SAFETY: GetLocalTime only fills the SYSTEMTIME it is given.
    let mut st = unsafe { std::mem::zeroed() };
    unsafe { GetLocalTime(&mut st) };
    let st: windows_sys::Win32::Foundation::SYSTEMTIME = st;
    ((st.wHour as u32 * 60 + st.wMinute as u32) * 60 + st.wSecond as u32) * 1000
        + st.wMilliseconds as u32
}

pub struct Transport {
    link: Box<dyn Link>,
    /// Replies to queries no longer waited for; skipped when they come.
    late: u32,
}

impl Transport {
    pub fn new(link: Box<dyn Link>) -> Transport {
        Transport { link, late: 0 }
    }

    fn send(&mut self, cmd: u8, args: &[u8], payload: &[u8], timeout_ms: u32) -> Result<(), Error> {
        let packet = turzx::packet(cmd, args, local_ms()).map_err(Error::Device)?;
        let mut data = Vec::with_capacity(packet.len() + payload.len());
        data.extend_from_slice(&packet);
        data.extend_from_slice(payload);
        self.link
            .write(data, timeout_ms)
            .map_err(|e| Error::Device(format!("USB error on command {cmd}: {e}")))
    }

    /// The reply to `cmd`, past the late ones to earlier queries.
    fn reply(&mut self, cmd: u8, timeout_ms: u32) -> Result<Vec<u8>, LinkError> {
        let mut reply = self.link.read(READ_LEN, timeout_ms)?;
        while self.late > 0 && reply.first() != Some(&cmd) {
            self.late -= 1;
            reply = self.link.read(READ_LEN, timeout_ms)?;
        }
        Ok(reply)
    }

    /// A command and its payload, accepted (0xC8) by the panel: its reply.
    pub fn command(
        &mut self,
        cmd: u8,
        args: &[u8],
        payload: &[u8],
        timeout_ms: u32,
    ) -> Result<Vec<u8>, Error> {
        self.send(cmd, args, payload, timeout_ms)?;
        let reply = self
            .reply(cmd, timeout_ms)
            .map_err(|e| Error::Device(format!("USB error on command {cmd}: {e}")))?;
        if reply.first() != Some(&cmd) {
            return Err(Error::Device(format!(
                "command {cmd}: unexpected reply {}",
                hex16(&reply)
            )));
        }
        if reply.get(1) != Some(&ACK) && reply.get(8) != Some(&ACK) {
            return Err(Error::Device(format!(
                "command {cmd} not accepted: {}",
                hex16(&reply)
            )));
        }
        Ok(reply)
    }

    /// A command whose reply carries data rather than the accepted mark (``ask``).
    pub fn ask(
        &mut self,
        cmd: u8,
        args: &[u8],
        timeout_ms: u32,
        echo: bool,
    ) -> Result<Vec<u8>, Error> {
        self.send(cmd, args, &[], timeout_ms)?;
        let reply = self
            .reply(cmd, timeout_ms)
            .map_err(|e| Error::Device(format!("USB error on command {cmd}: {e}")))?;
        if echo && reply.first() != Some(&cmd) {
            return Err(Error::Device(format!(
                "command {cmd}: unexpected reply {}",
                hex16(&reply)
            )));
        }
        Ok(reply)
    }

    /// A command and its payload whose reply may not come (``push``): the reply, or None.
    pub fn push(
        &mut self,
        cmd: u8,
        args: &[u8],
        payload: &[u8],
        timeout_ms: u32,
        reply_ms: u32,
    ) -> Result<Option<Vec<u8>>, Error> {
        self.send(cmd, args, payload, timeout_ms)?;
        match self.link.read(READ_LEN, reply_ms) {
            Ok(reply) => Ok(Some(reply)),
            Err(LinkError::Timeout) => Ok(None),
            Err(e) => Err(Error::Device(format!("USB error on command {cmd}: {e}"))),
        }
    }

    /// A status request (``query``): while the panel decodes video it sometimes answers late;
    /// then None, and the late answer is skipped when it comes.
    pub fn query(&mut self, cmd: u8, timeout_ms: u32) -> Result<Option<Vec<u8>>, Error> {
        self.send(cmd, &[], &[], timeout_ms)?;
        let reply = match self.reply(cmd, timeout_ms) {
            Ok(reply) => reply,
            Err(LinkError::Timeout) => {
                self.late = (self.late + 1).min(LATE_MOST);
                return Ok(None);
            }
            Err(e) => return Err(Error::Device(format!("USB error on command {cmd}: {e}"))),
        };
        if reply.first() != Some(&cmd) {
            return Err(Error::Device(format!(
                "command {cmd}: unexpected reply {}",
                hex16(&reply)
            )));
        }
        Ok(Some(reply))
    }

    /// Throw away replies left over from an earlier (crashed) session: how many.
    pub fn drain(&mut self, timeout_ms: u32, limit: u32) -> u32 {
        let mut dropped = 0;
        for _ in 0..limit {
            if self.link.read(READ_LEN, timeout_ms).is_err() {
                break;
            }
            dropped += 1;
        }
        self.late = 0;
        dropped
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;
    use std::sync::{Arc, Mutex};

    /// What a link was asked, and the replies it hands out (None: a read that times out).
    #[derive(Default)]
    pub struct Script {
        pub written: Vec<Vec<u8>>,
        pub replies: VecDeque<Option<Vec<u8>>>,
        pub reads: Vec<usize>,
    }

    pub struct Scripted(pub Arc<Mutex<Script>>);

    impl Link for Scripted {
        fn write(&mut self, data: Vec<u8>, _timeout_ms: u32) -> Result<(), LinkError> {
            self.0.lock().unwrap().written.push(data);
            Ok(())
        }

        fn read(&mut self, len: usize, _timeout_ms: u32) -> Result<Vec<u8>, LinkError> {
            let mut script = self.0.lock().unwrap();
            script.reads.push(len);
            match script.replies.pop_front() {
                Some(Some(reply)) => Ok(reply),
                _ => Err(LinkError::Timeout),
            }
        }
    }

    fn reply(cmd: u8, ack: bool) -> Option<Vec<u8>> {
        let mut r = vec![0u8; 512];
        r[0] = cmd;
        if ack {
            r[1] = ACK;
        }
        Some(r)
    }

    fn scripted(replies: Vec<Option<Vec<u8>>>) -> (Transport, Arc<Mutex<Script>>) {
        let script = Arc::new(Mutex::new(Script {
            replies: replies.into(),
            ..Default::default()
        }));
        (Transport::new(Box::new(Scripted(script.clone()))), script)
    }

    #[test]
    fn a_command_is_its_packet_and_payload_in_one_write_and_reads_past_the_zlp() {
        let (mut t, script) = scripted(vec![reply(121, true)]);
        t.command(121, &4u32.to_be_bytes(), b"abcd", 5000).unwrap();
        let script = script.lock().unwrap();
        assert_eq!(script.written.len(), 1);
        assert_eq!(script.written[0].len(), 512 + 4);
        assert_eq!(&script.written[0][512..], b"abcd");
        assert_eq!(&script.written[0][510..512], &turzx::TRAILER);
        assert_eq!(script.reads, vec![READ_LEN]);
    }

    #[test]
    fn a_reply_without_the_accepted_mark_or_to_another_command_fails() {
        let (mut t, _) = scripted(vec![reply(14, false)]);
        assert!(matches!(
            t.command(14, &[50], &[], 2000),
            Err(Error::Device(_))
        ));
        let (mut t, _) = scripted(vec![reply(15, true)]);
        assert!(matches!(
            t.command(14, &[50], &[], 2000),
            Err(Error::Device(_))
        ));
        let mut late_ack = vec![0u8; 512];
        late_ack[0] = 17;
        late_ack[8] = ACK; // (the mark may also stand at 8)
        let (mut t, _) = scripted(vec![Some(late_ack)]);
        assert!(t.command(17, &[], &[], 2000).is_ok());
    }

    #[test]
    fn a_late_answer_to_a_query_is_skipped_when_it_comes() {
        // the first 122 gets no answer in time; its answer comes before the 121's
        let (mut t, script) = scripted(vec![None, reply(122, false), reply(121, true)]);
        assert!(t.query(122, 400).unwrap().is_none());
        t.command(121, &0u32.to_be_bytes(), &[], 5000).unwrap();
        assert_eq!(script.lock().unwrap().reads.len(), 3);
        // without one waited for, another command's reply is no reason to read on
        let (mut t, _) = scripted(vec![reply(122, false)]);
        assert!(matches!(
            t.command(121, &[], &[], 5000),
            Err(Error::Device(_))
        ));
    }

    #[test]
    fn late_answers_are_counted_up_to_eight_and_a_drain_forgets_them() {
        let (mut t, _) = scripted(vec![None; 12]);
        for _ in 0..12 {
            assert!(t.query(122, 400).unwrap().is_none());
        }
        assert_eq!(t.late, 8);
        let (mut t, _) = scripted(vec![None, reply(1, false), reply(2, false)]);
        assert!(t.query(122, 400).unwrap().is_none());
        assert_eq!(t.drain(50, 64), 2); // (the two left over; then nothing came)
        assert_eq!(t.late, 0);
    }

    #[test]
    fn a_push_reads_once_and_takes_no_answer_as_none() {
        let (mut t, script) = scripted(vec![None]);
        assert!(t.push(102, &[], b"png", 5000, 200).unwrap().is_none());
        assert_eq!(script.lock().unwrap().reads, vec![READ_LEN]);
    }

    #[test]
    fn the_clock_field_is_milliseconds_of_the_day() {
        assert!(local_ms() < 86_400_000);
    }
}
