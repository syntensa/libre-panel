//! For a panel that shows a picture of its own again when it gets none for a while (base.py's
//! KeepShowing: the JONSBO DS916, DS339, a udisp display): the last picture goes again from a
//! thread of its own whenever nothing went out for `every` seconds. The driver sends under the
//! keeper's lock ([`Keeper::with`]) and notes it ([`Kept::sent`]); a fault of the thread comes
//! out at the driver's next [`Kept::check`].

use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use crate::base::{DeviceError, Result};

/// What the keeper guards: the driver's link and what it sends again.
pub struct Kept<T> {
    pub inner: T,
    last: Instant,
    fault: Option<DeviceError>,
    stop: bool,
}

impl<T> Kept<T> {
    /// sent: something went out just now.
    pub fn sent(&mut self) {
        self.last = Instant::now();
    }

    /// check: the keeper's fault, raised once.
    pub fn check(&mut self) -> Result<()> {
        match self.fault.take() {
            Some(fault) => Err(fault),
            None => Ok(()),
        }
    }
}

struct Shared<T> {
    state: Mutex<Kept<T>>,
    wake: Condvar,
}

impl<T> Shared<T> {
    fn lock(&self) -> MutexGuard<'_, Kept<T>> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }
}

/// The keeper of a driver's link `T`; `again` sends the last picture again.
pub struct Keeper<T: Send + 'static> {
    shared: Arc<Shared<T>>,
    every: f64,
    again: fn(&mut T) -> Result<()>,
    name: &'static str,
    thread: Option<JoinHandle<()>>,
}

impl<T: Send + 'static> Keeper<T> {
    pub fn new(
        every: f64,
        inner: T,
        again: fn(&mut T) -> Result<()>,
        name: &'static str,
    ) -> Keeper<T> {
        Keeper {
            shared: Arc::new(Shared {
                state: Mutex::new(Kept {
                    inner,
                    last: Instant::now(),
                    fault: None,
                    stop: true,
                }),
                wake: Condvar::new(),
            }),
            every,
            again,
            name,
            thread: None,
        }
    }

    /// with: `f` on the link, under the keeper's lock.
    pub fn with<R>(&self, f: impl FnOnce(&mut Kept<T>) -> R) -> R {
        f(&mut self.shared.lock())
    }

    /// How long without a picture before the last goes again (from the next start).
    pub fn set_every(&mut self, every: f64) {
        self.every = every;
    }

    pub fn start(&mut self) {
        self.stop();
        {
            let mut state = self.shared.lock();
            state.stop = false;
            state.fault = None;
            state.last = Instant::now();
        }
        let shared = Arc::clone(&self.shared);
        let every = self.every;
        let again = self.again;
        let tick = Duration::from_secs_f64((every / 2.0).min(0.25));
        self.thread = thread::Builder::new()
            .name(self.name.to_string())
            .spawn(move || loop {
                let state = shared.lock();
                let (mut state, _) = shared
                    .wake
                    .wait_timeout_while(state, tick, |state| !state.stop)
                    .unwrap_or_else(|e| e.into_inner());
                if state.stop {
                    return;
                }
                if state.last.elapsed().as_secs_f64() < every {
                    continue;
                }
                match again(&mut state.inner) {
                    Ok(()) => state.last = Instant::now(),
                    Err(fault) => {
                        state.fault = Some(fault);
                        return;
                    }
                }
            })
            .ok();
    }

    pub fn stop(&mut self) {
        self.shared.lock().stop = true;
        self.shared.wake.notify_all();
        if let Some(thread) = self.thread.take() {
            if thread.thread().id() != thread::current().id() {
                let _ = thread.join();
            }
        }
    }
}

impl<T: Send + 'static> Drop for Keeper<T> {
    fn drop(&mut self) {
        self.stop();
    }
}
