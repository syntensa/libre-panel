//! A text file as Python reads it: `Path.read_text(encoding="utf-8")`.

use std::fmt;

/// UnicodeDecodeError of the "utf-8" codec.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DecodeError {
    /// `exc.start`: the first byte that is not UTF-8
    pub start: usize,
    /// `exc.end`: one past the last byte the refusal is about
    pub end: usize,
    /// `exc.reason`
    pub reason: &'static str,
    /// the byte at `start`
    pub byte: u8,
}

impl fmt::Display for DecodeError {
    /// `str(exc)`
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let Self {
            start,
            end,
            reason,
            byte,
        } = self;
        if end - start == 1 {
            write!(
                f,
                "'utf-8' codec can't decode byte 0x{byte:02x} in position {start}: {reason}"
            )
        } else {
            write!(
                f,
                "'utf-8' codec can't decode bytes in position {start}-{}: {reason}",
                end - 1
            )
        }
    }
}

impl std::error::Error for DecodeError {}

/// `bytes.decode("utf-8")`: strictly, a byte order mark kept.
pub fn decode_utf8(bytes: &[u8]) -> Result<&str, DecodeError> {
    std::str::from_utf8(bytes).map_err(|e| {
        let start = e.valid_up_to();
        let (end, reason) = match e.error_len() {
            None => (bytes.len(), "unexpected end of data"),
            Some(n) => {
                let starts = matches!(bytes[start], 0xC2..=0xF4);
                let reason = if starts {
                    "invalid continuation byte"
                } else {
                    "invalid start byte"
                };
                (start + n, reason)
            }
        };
        DecodeError {
            start,
            end,
            reason,
            byte: bytes[start],
        }
    })
}

/// `read_text(encoding="utf-8")` of a file with these bytes: decoded strictly, and its line ends
/// ("\r\n", "\r") made "\n".
pub fn read_text(bytes: &[u8]) -> Result<String, DecodeError> {
    Ok(decode_utf8(bytes)?
        .replace("\r\n", "\n")
        .replace('\r', "\n"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn refuses_as_python_does() {
        let said = |bytes: &[u8]| read_text(bytes).unwrap_err().to_string();
        assert_eq!(
            said(b"ab\xff"),
            "'utf-8' codec can't decode byte 0xff in position 2: invalid start byte"
        );
        assert_eq!(
            said(b"\xe2\x82("),
            "'utf-8' codec can't decode bytes in position 0-1: invalid continuation byte"
        );
        assert_eq!(
            said(b"a\xe2\x82"),
            "'utf-8' codec can't decode bytes in position 1-2: unexpected end of data"
        );
        assert_eq!(read_text(b"a\r\nb\rc\n").unwrap(), "a\nb\nc\n");
    }
}
