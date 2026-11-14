//! A panel's link: the bytes to it and from it (devices/serial_link.py's SerialLink), a serial
//! port or, in the tests, a panel they simulate.

use crate::base::Result;

/// What a driver asks of its link. Its errors are DeviceErrors: the main loop connects again.
pub trait Link: Send {
    /// All of `data`, in order.
    fn write(&mut self, data: &[u8]) -> Result<()>;
    /// Up to `size` bytes: as many as came within the link's time (a second on a serial port,
    /// as pyserial's read waits); fewer, or none, where no more came.
    fn read(&mut self, size: usize) -> Result<Vec<u8>>;
    /// What came in and was not read yet, thrown away.
    fn drain_input(&mut self);
    /// The link let go (where it is the panel's place, another may take it).
    fn close(&mut self);
}
