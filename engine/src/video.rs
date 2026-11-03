//! The pictures into the panel's decoder, block by block, as devices/turzx_video.py's
//! ``VideoSession.send`` sends them: flow control on the panel's queue and the hang check of
//! SPUR II, with the same numbers.

use std::collections::VecDeque;
use std::time::{Duration, Instant};

use crate::transport::{Error, Transport};

pub const CMD_H264_BLOCK: u8 = 121;
pub const CMD_QUEUE_DEPTH: u8 = 122;
/// The panel's own block size; it answers 17 with 0 for it.
pub const DEFAULT_BLOCK: usize = 202_752;

const HIGH: u8 = 2; // a queue this deep starts waiting
const LOW: u8 = 1; // and the wait ends at this depth
const POLL_EVERY: u64 = 2; // blocks between depth queries
const SLOW_BLOCK: Duration = Duration::from_millis(20); // longer than a frame at 50 fps
const WAIT: Duration = Duration::from_millis(30);
const GIVE_UP: Duration = Duration::from_millis(1500);
// Hang detection: a healthy block takes about 1 ms. When the median of the last 40 is above
// 0.2 s, four depth readings 150 ms apart decide: above 20 and moving by 2 at most is a hang.
pub const HANG_WINDOW: usize = 40;
const HANG_MEDIAN: f64 = 0.2;
const HANG_READINGS: usize = 4;
const HANG_READING_GAP: Duration = Duration::from_millis(150);
const HANG_DEPTH: u8 = 20;
const HANG_SPREAD: u8 = 2;

/// What the video path counted, for the app's diagnostics and the bit rate "auto" (h264.py's
/// ``RateControl.judge``: waits, blocks and slow blocks).
#[derive(Default, Clone, Debug)]
pub struct Counts {
    pub blocks: u64,
    pub bytes: u64,
    pub waits: u64,
    pub slow_blocks: u64,
    pub max_depth: u8,
}

pub struct Video {
    pub block_size: usize,
    pub counts: Counts,
    send_times: VecDeque<f64>,
    /// The blocks the hang check looks at (HANG_WINDOW; fewer for the tests).
    pub hang_window: usize,
    sleep: fn(Duration),
}

impl Video {
    pub fn new(block_size: usize) -> Video {
        Video {
            block_size: if block_size == 0 {
                DEFAULT_BLOCK
            } else {
                block_size
            },
            counts: Counts::default(),
            send_times: VecDeque::with_capacity(HANG_WINDOW),
            hang_window: HANG_WINDOW,
            sleep: std::thread::sleep,
        }
    }

    /// One picture; pictures larger than a block (rare keyframes) take several.
    pub fn send(&mut self, transport: &mut Transport, picture: &[u8]) -> Result<(), Error> {
        for block in picture.chunks(self.block_size) {
            let began = Instant::now();
            transport.command(
                CMD_H264_BLOCK,
                &(block.len() as u32).to_be_bytes(),
                block,
                5000,
            )?;
            let took = began.elapsed();
            if self.send_times.len() >= self.hang_window {
                self.send_times.pop_front();
            }
            self.send_times.push_back(took.as_secs_f64());
            self.counts.blocks += 1;
            self.counts.bytes += block.len() as u64;
            if took > SLOW_BLOCK {
                self.counts.slow_blocks += 1;
            }
            if self.counts.blocks.is_multiple_of(POLL_EVERY) {
                if let Some(depth) = self.depth(transport)? {
                    if depth > HIGH {
                        self.wait_for_room(transport)?;
                    }
                }
            }
            self.check_hang(transport)?;
        }
        Ok(())
    }

    /// The panel's queue, as it says it (None: it did not answer in time).
    pub fn depth(&mut self, transport: &mut Transport) -> Result<Option<u8>, Error> {
        let reply = transport.query(CMD_QUEUE_DEPTH, 400)?;
        Ok(match reply {
            Some(reply) if reply.len() > 8 => {
                self.counts.max_depth = self.counts.max_depth.max(reply[8]);
                Some(reply[8])
            }
            _ => None,
        })
    }

    fn wait_for_room(&mut self, transport: &mut Transport) -> Result<(), Error> {
        self.counts.waits += 1;
        let started = Instant::now();
        while started.elapsed() < GIVE_UP {
            (self.sleep)(WAIT);
            match self.depth(transport)? {
                None => return Ok(()),
                Some(depth) if depth <= LOW => return Ok(()),
                Some(_) => {}
            }
        }
        Ok(())
    }

    /// A hang: blocks going slowly *and* the queue standing high without moving. The median,
    /// not one slow block: waiting for room makes single sends slow, and a high queue that
    /// moves drains by itself.
    fn check_hang(&mut self, transport: &mut Transport) -> Result<(), Error> {
        if self.send_times.len() < self.hang_window || median(&self.send_times) <= HANG_MEDIAN {
            return Ok(());
        }
        self.send_times.clear(); // decide once per window
        let mut readings = Vec::with_capacity(HANG_READINGS);
        for i in 0..HANG_READINGS {
            if i > 0 {
                (self.sleep)(HANG_READING_GAP);
            }
            if let Some(depth) = self.depth(transport)? {
                readings.push(depth);
            }
        }
        eprintln!("libre-panel-engine: video blocks go slowly; queue depth {readings:?}");
        if readings.len() >= 2 {
            let (low, high) = (
                *readings.iter().min().unwrap(),
                *readings.iter().max().unwrap(),
            );
            if high > HANG_DEPTH && high - low <= HANG_SPREAD {
                return Err(Error::Hung(high));
            }
        }
        Ok(())
    }
}

/// As Python's statistics.median: the middle one, or the mean of the two in the middle.
fn median(values: &VecDeque<f64>) -> f64 {
    let mut sorted: Vec<f64> = values.iter().copied().collect();
    sorted.sort_by(|a, b| a.total_cmp(b));
    let n = sorted.len();
    if n % 2 == 1 {
        sorted[n / 2]
    } else {
        (sorted[n / 2 - 1] + sorted[n / 2]) / 2.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::link::{Link, LinkError};
    use crate::transport::ACK;
    use std::sync::{Arc, Mutex};

    /// A panel whose queue says what the test wants and whose blocks take as long as it says.
    struct Panel {
        commands: Vec<u8>,
        sizes: Vec<usize>,
        depths: VecDeque<u8>,
        block_delay: Duration,
        pending: VecDeque<Vec<u8>>,
    }

    struct Wire(Arc<Mutex<Panel>>);

    impl Link for Wire {
        fn write(&mut self, data: Vec<u8>, _timeout_ms: u32) -> Result<(), LinkError> {
            let mut panel = self.0.lock().unwrap();
            let cmd = crate::turzx::tests::decoded(&data[..512])[0];
            panel.commands.push(cmd);
            let mut reply = vec![0u8; 512];
            reply[0] = cmd;
            reply[1] = ACK;
            if cmd == CMD_H264_BLOCK {
                panel.sizes.push(data.len() - 512);
                std::thread::sleep(panel.block_delay);
            }
            if cmd == CMD_QUEUE_DEPTH {
                reply[8] = panel.depths.pop_front().unwrap_or(0);
            }
            panel.pending.push_back(reply);
            Ok(())
        }

        fn read(&mut self, _len: usize, _timeout_ms: u32) -> Result<Vec<u8>, LinkError> {
            self.0
                .lock()
                .unwrap()
                .pending
                .pop_front()
                .ok_or(LinkError::Timeout)
        }
    }

    fn panel(depths: &[u8], block_delay: Duration) -> (Transport, Arc<Mutex<Panel>>) {
        let panel = Arc::new(Mutex::new(Panel {
            commands: vec![],
            sizes: vec![],
            depths: depths.iter().copied().collect(),
            block_delay,
            pending: VecDeque::new(),
        }));
        (Transport::new(Box::new(Wire(panel.clone()))), panel)
    }

    fn no_sleep(_: Duration) {}

    #[test]
    fn a_picture_larger_than_a_block_takes_several_and_the_queue_is_asked_every_second() {
        let (mut t, panel) = panel(&[], Duration::ZERO);
        let mut video = Video::new(1000);
        video.sleep = no_sleep;
        video.send(&mut t, &[7u8; 2500]).unwrap();
        let panel = panel.lock().unwrap();
        assert_eq!(panel.sizes, vec![1000, 1000, 500]);
        assert_eq!(panel.commands, vec![121, 121, 122, 121]);
        assert_eq!(video.counts.blocks, 3);
        assert_eq!(video.counts.bytes, 2500);
    }

    #[test]
    fn a_deep_queue_is_waited_for_until_it_is_down_to_one() {
        let (mut t, panel) = panel(&[3, 3, 2, 1], Duration::ZERO);
        let mut video = Video::new(DEFAULT_BLOCK);
        video.sleep = no_sleep;
        video.send(&mut t, b"a").unwrap();
        video.send(&mut t, b"b").unwrap();
        assert_eq!(video.counts.waits, 1);
        assert_eq!(video.counts.max_depth, 3);
        let commands = panel.lock().unwrap().commands.clone();
        assert_eq!(commands, vec![121, 121, 122, 122, 122, 122]);
    }

    #[test]
    fn slow_blocks_with_a_queue_stuck_high_are_a_hang_and_a_moving_one_is_not() {
        // 40 blocks at 0.2 s each would take 8 s; the median only needs to be above 0.2 s,
        // so the clock is the test's: every block is said to take 0.25 s
        let (mut t, _) = panel(&[], Duration::ZERO);
        let mut video = Video::new(DEFAULT_BLOCK);
        video.sleep = no_sleep;
        video.send_times = std::iter::repeat_n(0.25, HANG_WINDOW).collect();
        // (the depth asked after every second block: 0; then the four readings)
        let mut stuck = panel(&[25, 25, 24, 25], Duration::ZERO);
        let mut moving = panel(&[25, 18, 12, 6], Duration::ZERO);
        let mut v = Video::new(DEFAULT_BLOCK);
        v.sleep = no_sleep;
        v.send_times = video.send_times.clone();
        assert!(matches!(v.check_hang(&mut stuck.0), Err(Error::Hung(25))));
        v.send_times = video.send_times.clone();
        assert!(v.check_hang(&mut moving.0).is_ok());
        // the window is cleared once it decided
        assert!(v.send_times.is_empty());
        let _ = (&mut t, &mut stuck.1, &mut moving.1);
    }

    #[test]
    fn the_median_is_pythons() {
        let odd: VecDeque<f64> = [3.0, 1.0, 2.0].into_iter().collect();
        let even: VecDeque<f64> = [4.0, 1.0, 3.0, 2.0].into_iter().collect();
        assert_eq!(median(&odd), 2.0);
        assert_eq!(median(&even), 2.5);
    }
}
