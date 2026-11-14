//! What plays on Windows: the current session of the system media transport controls, by its
//! events (sensors/media.py's _WindowsBackend).
//!
//! Windows tells when the session, the track or the playback changes, and the source looks
//! then, instead of every second. A timeline event (the position: a seek, or one of those
//! players send every few seconds) brings a look at the timeline alone, a few milliseconds,
//! and changes the track only where the position is not where it was expected
//! (`SEEK_SECONDS`). The interval is the safety poll for a notice that went missing.

use std::collections::BTreeSet;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use windows::core::{Result as WinResult, HSTRING};
use windows::Foundation::TypedEventHandler;
use windows::Media::Control::{
    GlobalSystemMediaTransportControlsSession as Session,
    GlobalSystemMediaTransportControlsSessionManager as Manager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
};
use windows::Storage::Streams::{
    Buffer, DataReader, IRandomAccessStreamReference, InputStreamOptions,
};
use windows::Win32::System::WinRT::{RoInitialize, RO_INIT_MULTITHREADED};

use crate::media::{
    app_name, Backend, Cover, Track, COVER_RETRY_SECONDS, MAX_COVER, POLL_SECONDS, SAFETY_SECONDS,
    SEEK_SECONDS,
};

const SESSION_CHANGED: &str = "current_session_changed";
const PROPERTIES: &str = "media_properties_changed";
const PLAYBACK: &str = "playback_info_changed";
const TIMELINE: &str = "timeline_properties_changed";
/// 1 January 2001 in the hundred nanoseconds Windows counts from 1601: a time before it is
/// none a player set
const YEAR_2001: i64 = 146_097 * 86_400 * 10_000_000;
/// 1 January 1970 in seconds from 1601
const EPOCH_SECONDS: f64 = 11_644_473_600.0;

/// What Windows told since the last look, noted in threads of Windows.
#[derive(Default)]
struct News {
    kinds: BTreeSet<&'static str>,
    /// the track's properties changed since the cover was read
    new_properties: bool,
    /// the current session changed: listen to it again
    new_session: bool,
    wake: Option<Arc<dyn Fn() + Send + Sync>>,
}

/// A value Windows may not have (a null it gives as an error without a code).
fn maybe<T>(found: WinResult<T>) -> Result<Option<T>, String> {
    match found {
        Ok(value) => Ok(Some(value)),
        Err(why) if why.code().is_ok() => Ok(None),
        Err(why) => Err(why.message()),
    }
}

fn text(found: WinResult<HSTRING>) -> String {
    found.map(|text| text.to_string_lossy()).unwrap_or_default()
}

/// What is listened to of a session, to let go of it again.
struct Listening {
    session: Session,
    properties: Option<i64>,
    playback: Option<i64>,
    timeline: Option<i64>,
}

pub struct Controls {
    news: Arc<Mutex<News>>,
    manager: Option<Manager>,
    manager_token: Option<i64>,
    /// every event could be subscribed
    manager_listens: bool,
    session_listens: bool,
    /// the AppUserModelID of the session listened to (none: no session)
    session_id: Option<String>,
    listening: Option<Listening>,
    /// what the last full look found
    last: Option<Track>,
    timeline_only: bool,
    cover_key: Option<(String, String)>,
    cover: Option<Arc<Vec<u8>>>,
    /// when a cover was last read
    cover_asked: Option<f64>,
}

impl Controls {
    pub fn new() -> Result<Controls, String> {
        // SAFETY: this thread talks to Windows: it joins the multi-threaded apartment (an
        // error says it has joined one already).
        unsafe {
            let _ = RoInitialize(RO_INIT_MULTITHREADED);
        }
        Ok(Controls {
            news: Arc::new(Mutex::new(News::default())),
            manager: None,
            manager_token: None,
            manager_listens: true,
            session_listens: true,
            session_id: None,
            listening: None,
            last: None,
            timeline_only: false,
            cover_key: None,
            cover: None,
            cover_asked: None,
        })
    }

    /// A handler that notes the event `name` and wakes the reader.
    fn handler<S, A>(&self, name: &'static str) -> TypedEventHandler<S, A>
    where
        S: windows::core::RuntimeType + 'static,
        A: windows::core::RuntimeType + 'static,
    {
        let news = self.news.clone();
        TypedEventHandler::new(move |_, _| {
            let wake = news.lock().ok().and_then(|mut news| {
                news.kinds.insert(name);
                news.new_properties |= name == PROPERTIES;
                news.new_session |= name == SESSION_CHANGED;
                news.wake.clone()
            });
            if let Some(wake) = wake {
                wake();
            }
            Ok(())
        })
    }

    fn unfollow(&mut self) {
        if let Some(heard) = self.listening.take() {
            // (the session may be gone already)
            if let Some(token) = heard.properties {
                let _ = heard.session.RemoveMediaPropertiesChanged(token);
            }
            if let Some(token) = heard.playback {
                let _ = heard.session.RemovePlaybackInfoChanged(token);
            }
            if let Some(token) = heard.timeline {
                let _ = heard.session.RemoveTimelinePropertiesChanged(token);
            }
        }
    }

    /// Listen to the session that is current now, and to no other. Another player is another
    /// session; the same player after a restart is a new session object, which
    /// `current_session_changed` tells.
    fn follow(&mut self, session: Option<&Session>, app_id: Option<&str>) {
        let new_session = self
            .news
            .lock()
            .is_ok_and(|mut news| std::mem::take(&mut news.new_session));
        if app_id == self.session_id.as_deref() && !new_session {
            return;
        }
        self.unfollow();
        self.session_id = app_id.map(str::to_string);
        self.session_listens = true;
        if let Some(session) = session {
            let heard = Listening {
                session: session.clone(),
                properties: session
                    .MediaPropertiesChanged(&self.handler(PROPERTIES))
                    .ok(),
                playback: session.PlaybackInfoChanged(&self.handler(PLAYBACK)).ok(),
                timeline: session
                    .TimelinePropertiesChanged(&self.handler(TIMELINE))
                    .ok(),
            };
            // (an older Windows may not have an event: the safety poll then looks more often)
            self.session_listens =
                heard.properties.is_some() && heard.playback.is_some() && heard.timeline.is_some();
            self.listening = Some(heard);
        }
    }

    /// (position, length, the time of the position) of the session's timeline.
    fn timeline(session: &Session, playing: bool, now: f64) -> (Option<f64>, Option<f64>, f64) {
        let seconds = |span: windows::Foundation::TimeSpan| span.Duration as f64 / 1e7;
        // (a player without a timeline, a live stream: none)
        let Ok(timeline) = session.GetTimelineProperties() else {
            return (None, None, now);
        };
        let spans = (
            timeline.StartTime(),
            timeline.EndTime(),
            timeline.Position(),
        );
        let (Ok(start), Ok(end), Ok(position)) = spans else {
            return (None, None, now);
        };
        let start = seconds(start);
        let duration = seconds(end) - start;
        let position = 0.0f64.max(seconds(position) - start);
        if duration <= 0.0 {
            return (None, None, now);
        }
        let mut at = now;
        if let (Ok(updated), true) = (timeline.LastUpdatedTime(), playing) {
            // the position is as of its last update
            if updated.UniversalTime >= YEAR_2001 {
                let clock = SystemTime::now().duration_since(UNIX_EPOCH);
                let clock = clock.map_or(0.0, |since| since.as_secs_f64()) + EPOCH_SECONDS;
                at -= 0.0f64.max(clock - updated.UniversalTime as f64 / 1e7);
            }
        }
        (Some(position.min(duration)), Some(duration), at)
    }

    /// The timeline alone: the track as it was, unless the position is not where it was
    /// expected (a seek): then with the position the player says.
    fn moved(&mut self, track: Track, now: f64) -> Result<Track, String> {
        let Some(manager) = &self.manager else {
            return Ok(track);
        };
        let Some(session) = maybe(manager.GetCurrentSession())? else {
            return Ok(track);
        };
        let playing = track.state == "playing";
        let (position, duration, at) = Controls::timeline(&session, playing, now);
        let (Some(position), Some(before)) = (position, track.position) else {
            return Ok(track);
        };
        let passed = if playing {
            0.0f64.max(at - track.at)
        } else {
            0.0
        };
        if (position - (before + passed)).abs() <= SEEK_SECONDS {
            return Ok(track); // nothing downstream changes
        }
        let moved = Track {
            position: Some(position),
            duration,
            at,
            ..track
        };
        self.last = Some(moved.clone());
        Ok(moved)
    }

    fn read_cover(reference: &IRandomAccessStreamReference) -> Option<Arc<Vec<u8>>> {
        // (a missing cover is no error)
        let stream = reference.OpenReadAsync().ok()?.get().ok()?;
        let size = stream.Size().ok()?.min(MAX_COVER as u64) as u32;
        let buffer = Buffer::Create(size).ok()?;
        let filled = stream
            .ReadAsync(&buffer, size, InputStreamOptions::ReadAhead)
            .ok()?
            .get()
            .ok()?;
        let mut data = vec![0u8; filled.Length().ok()? as usize];
        DataReader::FromBuffer(&filled)
            .ok()?
            .ReadBytes(&mut data)
            .ok()?;
        Some(Arc::new(data))
    }

    fn full_look(
        &mut self,
        want_cover: bool,
        news: bool,
        now: &dyn Fn() -> f64,
    ) -> Result<Option<Track>, String> {
        let manager = match &self.manager {
            Some(manager) => manager.clone(),
            None => {
                let asked = Manager::RequestAsync().map_err(|why| why.message())?;
                let manager = asked.get().map_err(|why| why.message())?;
                self.manager_token = manager
                    .CurrentSessionChanged(&self.handler(SESSION_CHANGED))
                    .ok();
                self.manager_listens = self.manager_token.is_some();
                self.manager = Some(manager.clone());
                manager
            }
        };
        let session = maybe(manager.GetCurrentSession())?;
        let app_id = session
            .as_ref()
            .map(|session| text(session.SourceAppUserModelId()));
        self.follow(session.as_ref(), app_id.as_deref());
        let (Some(session), Some(app_id)) = (session, app_id) else {
            return Ok(None);
        };
        let asked = session
            .TryGetMediaPropertiesAsync()
            .map_err(|why| why.message())?;
        let Some(props) = maybe(asked.get())? else {
            return Ok(None);
        };
        let title = text(props.Title());
        if title.is_empty() {
            return Ok(None);
        }
        let status = session
            .GetPlaybackInfo()
            .and_then(|info| info.PlaybackStatus())
            .map_err(|why| why.message())?;
        let state = match status {
            PlaybackStatus::Playing => "playing",
            PlaybackStatus::Paused => "paused",
            _ => "stopped",
        };
        let (position, duration, at) = Controls::timeline(&session, state == "playing", now());
        let artist = text(props.Artist());
        let mut cover = None;
        let thumbnail = props.Thumbnail().ok().filter(|_| want_cover);
        if let Some(thumbnail) = thumbnail {
            let key = (title.clone(), artist.clone());
            let now = now();
            let retry = self.cover.is_none()
                && self
                    .cover_asked
                    .is_none_or(|asked| now - asked >= COVER_RETRY_SECONDS);
            // a new track, or the player says the picture changed, or it could not be read
            if self.cover_key.as_ref() != Some(&key) || news || retry {
                self.cover_key = Some(key);
                self.cover_asked = Some(now);
                self.cover = Controls::read_cover(&thumbnail);
            }
            cover = self.cover.clone();
        } else if !want_cover {
            self.cover_key = None; // nobody shows it: read it again when somebody does
        }
        Ok(Some(Track {
            state: state.into(),
            title,
            artist,
            album: text(props.AlbumTitle()),
            position,
            duration,
            cover: cover.map(Cover::Picture),
            source: app_name(&app_id),
            at,
            app_id,
        }))
    }

    fn look(&mut self, want_cover: bool, now: &dyn Fn() -> f64) -> Result<Option<Track>, String> {
        // Taken before looking: news that comes while we look stays for the next look.
        let (kinds, news, new_session) = match self.news.lock() {
            Ok(mut held) => (
                std::mem::take(&mut held.kinds),
                std::mem::take(&mut held.new_properties),
                held.new_session,
            ),
            Err(_) => (BTreeSet::new(), false, false),
        };
        self.timeline_only = kinds.len() == 1 && kinds.contains(TIMELINE) && self.last.is_some();
        if let (true, Some(track), false) = (self.timeline_only, &self.last, new_session) {
            if self.manager.is_some() {
                return self.moved(track.clone(), now()).map(Some);
            }
        }
        self.timeline_only = false;
        self.last = self.full_look(want_cover, news, now)?;
        Ok(self.last.clone())
    }
}

impl Backend for Controls {
    fn via(&self) -> String {
        "Windows media controls".into()
    }

    fn poll(&mut self, want_cover: bool, now: &dyn Fn() -> f64) -> Result<Option<Track>, String> {
        let found = self.look(want_cover, now);
        if found.is_err() {
            // the manager and the subscriptions may be unusable now: start anew
            self.close();
        }
        found
    }

    fn interval(&self) -> f64 {
        if self.manager_listens && self.session_listens {
            SAFETY_SECONDS
        } else {
            POLL_SECONDS
        }
    }

    fn timeline_only(&self) -> bool {
        self.timeline_only
    }

    fn bind(&mut self, wake: Arc<dyn Fn() + Send + Sync>) {
        if let Ok(mut news) = self.news.lock() {
            news.wake = Some(wake);
        }
    }

    fn close(&mut self) {
        self.unfollow();
        if let (Some(manager), Some(token)) = (self.manager.take(), self.manager_token.take()) {
            let _ = manager.RemoveCurrentSessionChanged(token);
        }
        self.session_id = None;
    }
}
