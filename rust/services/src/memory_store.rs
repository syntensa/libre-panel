//! The memory's database: one SQLite file in WAL mode, written by a single thread from a
//! bounded queue, so that nothing that samples ever waits for the disk
//! (services/memory_store.py).
//!
//! Readers (the cooling analysis, a console page) open the file read only, next to the
//! writer; WAL mode lets them. Everything stored here stays on this computer.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, RecvTimeoutError, SyncSender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use lp_py::{Dict, Value};
use rusqlite::types::{Value as Cell, ValueRef};
use rusqlite::{Connection, OpenFlags};

pub const SCHEMA_VERSION: u32 = 2;
pub const DATABASE: &str = "memory.db";
/// jobs; at about one a second this is more than an hour of a stuck disk
pub const QUEUE_SIZE: usize = 5000;
/// a database that cannot be opened is tried again after this long
pub const RETRY_S: f64 = 30.0;

/// The numbers kept per minute (means), and of those the highest value too.
pub const MEANS: [&str; 14] = [
    "cpu_load",
    "gpu_load",
    "cpu_temp",
    "gpu_temp",
    "gpu_hotspot",
    "cpu_power",
    "gpu_power",
    "cpu_clock",
    "gpu_clock",
    "mem_used",
    "vram_used",
    "net_down",
    "net_up",
    "volume",
];
pub const PEAKS: [&str; 5] = [
    "cpu_temp",
    "gpu_temp",
    "gpu_hotspot",
    "cpu_power",
    "gpu_power",
];
/// (power: the PC's, of the power service)
pub const SESSION_PEAKS: [&str; 7] = [
    "cpu_temp",
    "gpu_temp",
    "gpu_hotspot",
    "cpu_power",
    "gpu_power",
    "vram_used",
    "power",
];
/// What every frame of a round came to (the game.* readings of the same names,
/// sensors/presentmon)
pub const FRAME_FIGURES: [&str; 5] = [
    "fps_avg",
    "fps_1low",
    "fps_01low",
    "frametime_avg",
    "frametime_max",
];
/// What the cooling state holds as numbers; the other temperatures are in a column of their
/// own.
pub const COOLING_VALUES: [&str; 7] = [
    "cpu_temp",
    "cpu_power",
    "cpu_load",
    "gpu_temp",
    "gpu_hotspot",
    "gpu_power",
    "gpu_load",
];

fn columns(names: &[&str], suffix: &str) -> String {
    let columns: Vec<String> = names
        .iter()
        .map(|name| format!("{name}{suffix} REAL"))
        .collect();
    columns.join(", ")
}

/// The tables, as memory_store.py writes them.
pub fn schema() -> String {
    format!(
        r#"
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS minutes (
    ts INTEGER PRIMARY KEY,          -- start of the minute, Unix seconds
    samples INTEGER, mode TEXT, game TEXT,
    {means},
    {peaks},
    fps REAL, fps_low REAL,
    music_s INTEGER,                 -- how long music played: never what
    outside_temp REAL,
    fans TEXT                        -- {{"name": rpm, ...}}
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,          -- start in milliseconds, given when the game began
    game TEXT, program TEXT, started INTEGER, ended INTEGER, duration_s INTEGER,
    fps_s INTEGER, fps_mean REAL, fps_p01 REAL, fps_low_mean REAL, frametime_mean REAL,
    levels TEXT,                     -- {{"edges": [...], "seconds": [...]}}
    limit_cpu_s INTEGER, limit_gpu_s INTEGER, limit_cap_s INTEGER,
    {session_peaks},
    outside_temp REAL, closed INTEGER,
    {figures},
    course TEXT                      -- {{"seconds": s, "cpu_temp": [...], "gpu_temp": [...]}}
);
CREATE INDEX IF NOT EXISTS sessions_started ON sessions(started);
CREATE TABLE IF NOT EXISTS cooling (
    ts INTEGER PRIMARY KEY,          -- since when this state held, Unix seconds
    mode TEXT, game TEXT,            -- "normal" or "game"; which game
    {cooling},
    outside_temp REAL,
    temps TEXT,                      -- more temperatures: {{"reading key": degrees}}
    fans TEXT,                       -- {{"name": [rpm, duty %]}}
    sources TEXT                     -- {{"curve": degrees}}: what each FanControl curve reads
);
CREATE TABLE IF NOT EXISTS fancontrol (
    ts INTEGER PRIMARY KEY,          -- since when this setting held
    fingerprint TEXT,                -- of the curves and fans
    state TEXT                       -- fancontrol.state_of(): curves and fans
);
"#,
        means = columns(&MEANS, ""),
        peaks = columns(&PEAKS, "_max"),
        session_peaks = columns(&SESSION_PEAKS, "_max"),
        figures = columns(&FRAME_FIGURES, ""),
        cooling = columns(&COOLING_VALUES, ""),
    )
}

/// Columns that came after the first version, added to a file made before them (the rows it
/// has keep NULL there: nothing was measured).
fn added() -> Vec<(&'static str, Vec<String>)> {
    let mut columns = vec!["power_max REAL".to_string()];
    columns.extend(FRAME_FIGURES.iter().map(|name| format!("{name} REAL")));
    columns.push("course TEXT".into());
    vec![("sessions", columns)]
}

fn add_columns(connection: &Connection) -> rusqlite::Result<()> {
    for (table, columns) in added() {
        let mut there = Vec::new();
        {
            let mut asked = connection.prepare(&format!("PRAGMA table_info({table})"))?;
            let mut rows = asked.query([])?;
            while let Some(row) = rows.next()? {
                there.push(row.get::<_, String>(1)?);
            }
        }
        for column in columns {
            let name = column.split_whitespace().next().unwrap_or("");
            if !there.iter().any(|known| known == name) {
                connection.execute(&format!("ALTER TABLE {table} ADD COLUMN {column}"), [])?;
            }
        }
    }
    Ok(())
}

/// read_only: the database for reading only (the writer may be at work; WAL lets both).
pub fn read_only(path: &std::path::Path) -> rusqlite::Result<Connection> {
    Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)
}

/// A value as SQLite takes it from Python: None as NULL, a bool as 0 or 1.
pub fn cell(value: &Value) -> Result<Cell, String> {
    Ok(match value {
        Value::None => Cell::Null,
        Value::Bool(on) => Cell::Integer(i64::from(*on)),
        Value::Int(n) => Cell::Integer(
            i64::try_from(n)
                .map_err(|_| "OverflowError: Python int too large to convert to SQLite INTEGER")?,
        ),
        Value::Float(x) => Cell::Real(*x),
        Value::Str(text) => Cell::Text(text.clone()),
        other => {
            return Err(format!(
                "ProgrammingError: type '{}' is not supported",
                other.type_name()
            ))
        }
    })
}

/// What SQLite holds as Python reads it: NULL as None.
pub fn value(cell: ValueRef) -> Value {
    match cell {
        ValueRef::Null => Value::None,
        ValueRef::Integer(n) => Value::int(n),
        ValueRef::Real(x) => Value::Float(x),
        ValueRef::Text(text) => Value::Str(String::from_utf8_lossy(text).into_owned()),
        ValueRef::Blob(_) => Value::None,
    }
}

/// The rows a statement gives, each as a dict by its columns' names (sqlite3.Row as a dict).
pub fn rows(connection: &Connection, sql: &str, params: &[Value]) -> Result<Vec<Dict>, String> {
    let wrong = |why: rusqlite::Error| format!("OperationalError: {why}");
    let cells: Result<Vec<Cell>, String> = params.iter().map(cell).collect();
    let mut asked = connection.prepare(sql).map_err(wrong)?;
    let names: Vec<String> = asked.column_names().iter().map(|n| (*n).into()).collect();
    let mut found = Vec::new();
    let mut given = asked
        .query(rusqlite::params_from_iter(cells?))
        .map_err(wrong)?;
    while let Some(row) = given.next().map_err(wrong)? {
        let mut dict = Dict::default();
        for (at, name) in names.iter().enumerate() {
            dict.insert(name.clone(), value(row.get_ref(at).map_err(wrong)?));
        }
        found.push(dict);
    }
    Ok(found)
}

/// Something to do with the connection, on the store's thread.
pub type Work = Box<dyn FnOnce(&Connection) -> Result<(), String> + Send>;

/// What the store's thread is given to do.
pub enum Job {
    Sql(String, Vec<Value>),
    Run(Work),
}

struct Shared {
    path: std::path::PathBuf,
    retry_s: f64,
    began: Instant,
    written: AtomicU64,
    error: Mutex<Option<String>>,
    /// what there is to say for the log
    notes: Mutex<Vec<String>>,
    closing: AtomicBool,
}

impl Shared {
    fn say(&self, error: Option<String>) {
        if let Ok(mut held) = self.error.lock() {
            *held = error;
        }
    }

    fn failed(&self, text: String) {
        let known = (self.error.lock()).is_ok_and(|held| held.as_deref() == Some(&text));
        if !known {
            if let Ok(mut notes) = self.notes.lock() {
                notes.push(format!("memory: {text}"));
            }
        }
        self.say(Some(text));
    }

    fn open(&self, retry_at: &mut f64) -> Option<Connection> {
        let now = self.began.elapsed().as_secs_f64();
        if now < *retry_at {
            return None;
        }
        let open = || -> rusqlite::Result<Connection> {
            let connection = Connection::open(&self.path)?;
            connection.query_row("PRAGMA journal_mode=WAL", [], |_| Ok(()))?;
            // (safe against a crash)
            connection.execute_batch("PRAGMA synchronous=NORMAL")?;
            connection.execute_batch(&schema())?;
            add_columns(&connection)?;
            connection.execute(
                "INSERT OR REPLACE INTO meta VALUES ('schema', ?)",
                [SCHEMA_VERSION.to_string()],
            )?;
            Ok(connection)
        };
        match open() {
            Ok(connection) => Some(connection),
            Err(why) => {
                *retry_at = self.began.elapsed().as_secs_f64() + self.retry_s;
                self.failed(format!("the database cannot be used: {why}"));
                None
            }
        }
    }

    fn run(&self, jobs: &Receiver<Job>) {
        let mut connection: Option<Connection> = None;
        let mut retry_at = f64::NEG_INFINITY;
        loop {
            let job = match jobs.recv_timeout(Duration::from_millis(200)) {
                Ok(job) => job,
                Err(RecvTimeoutError::Timeout) if !self.closing.load(Ordering::Relaxed) => continue,
                Err(_) => break,
            };
            if connection.is_none() {
                connection = self.open(&mut retry_at);
            }
            let Some(open) = &connection else {
                continue; // the job is lost; a stuck database must not fill the memory
            };
            let done = match job {
                Job::Run(job) => job(open),
                Job::Sql(sql, params) => {
                    let cells: Result<Vec<Cell>, String> = params.iter().map(cell).collect();
                    cells.and_then(|cells| {
                        open.execute(&sql, rusqlite::params_from_iter(cells))
                            .map_err(|why| format!("OperationalError: {why}"))?;
                        self.written.fetch_add(1, Ordering::Relaxed);
                        Ok(())
                    })
                }
            };
            match done {
                Ok(()) => self.say(None),
                Err(why) => self.failed(why),
            }
        }
    }
}

/// Runs what it is given on one thread, in order: SQL statements, or functions that get the
/// connection (the round report reads the session it has just seen written).
///
/// `put` and `run` return at once; when the queue is full (the disk is stuck) the job is
/// dropped and `error` says so, since the memory must never hold the panel back.
pub struct Store {
    shared: Arc<Shared>,
    jobs: SyncSender<Job>,
    waiting: Mutex<Option<Receiver<Job>>>,
    ended: Mutex<Option<Receiver<()>>>,
}

impl Store {
    pub fn new(path: &std::path::Path) -> Store {
        Store::sized(path, QUEUE_SIZE, RETRY_S)
    }

    pub fn sized(path: &std::path::Path, size: usize, retry_s: f64) -> Store {
        let (jobs, waiting) = sync_channel(size);
        Store {
            shared: Arc::new(Shared {
                path: path.to_path_buf(),
                retry_s,
                began: Instant::now(),
                written: AtomicU64::new(0),
                error: Mutex::new(None),
                notes: Mutex::new(Vec::new()),
                closing: AtomicBool::new(false),
            }),
            jobs,
            waiting: Mutex::new(Some(waiting)),
            ended: Mutex::new(None),
        }
    }

    pub fn path(&self) -> &std::path::Path {
        &self.shared.path
    }

    /// How many statements were written.
    pub fn written(&self) -> u64 {
        self.shared.written.load(Ordering::Relaxed)
    }

    /// What is wrong, if anything is.
    pub fn error(&self) -> Option<String> {
        self.shared.error.lock().ok()?.clone()
    }

    /// What there is to say for the log since it was last asked.
    pub fn notes(&self) -> Vec<String> {
        (self.shared.notes.lock())
            .map_or_else(|_| Vec::new(), |mut notes| std::mem::take(&mut notes))
    }

    pub fn start(&self) {
        let Some(waiting) = self.waiting.lock().ok().and_then(|mut held| held.take()) else {
            return;
        };
        let shared = Arc::clone(&self.shared);
        let (done, ended) = sync_channel(1);
        let thread = std::thread::Builder::new().name("memory-store".into());
        let started = thread.spawn(move || {
            shared.run(&waiting);
            let _ = done.send(());
        });
        if started.is_ok() {
            if let Ok(mut held) = self.ended.lock() {
                *held = Some(ended);
            }
        }
    }

    pub fn put(&self, sql: &str, params: Vec<Value>) {
        self.enqueue(Job::Sql(sql.to_string(), params));
    }

    pub fn run(&self, job: Work) {
        self.enqueue(Job::Run(job));
    }

    fn enqueue(&self, job: Job) {
        if self.jobs.try_send(job).is_err() {
            let full = "the queue is full: the disk is too slow or the file is locked";
            self.shared.say(Some(full.into()));
        }
    }

    /// Write what is queued, then end; gives up waiting after `timeout` seconds.
    pub fn stop(&self, timeout: f64) {
        self.shared.closing.store(true, Ordering::Relaxed);
        if let Some(ended) = self.ended.lock().ok().and_then(|mut held| held.take()) {
            let _ = ended.recv_timeout(Duration::from_secs_f64(timeout));
        }
    }
}

// -- reading --------------------------------------------------------------------------------------

/// last_fingerprint: the mark of the FanControl state stored last ("" for none yet, or no
/// database).
pub fn last_fingerprint(path: &std::path::Path) -> String {
    let asked = "SELECT fingerprint FROM fancontrol ORDER BY ts DESC LIMIT 1";
    let read = || -> rusqlite::Result<Option<String>> {
        let connection = read_only(path)?;
        let mut asked = connection.prepare(asked)?;
        let mut rows = asked.query([])?;
        match rows.next()? {
            Some(row) => row.get::<_, Option<String>>(0),
            None => Ok(None),
        }
    };
    read().ok().flatten().unwrap_or_default()
}
