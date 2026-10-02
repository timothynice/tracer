//! The app's side of the trace worker. One worker runs at a time (Auto at 2048 px peaks near 11 GB; two at
//! once is a Mac swapping). A newer job for an image replaces its queued job, or kills its running one; `cancel`
//! kills or dequeues. A worker that exits without an answer is `engine_crashed`, or `cancelled` where this side
//! killed it. The running job is registered before its worker is spawned, so a cancel never misses it.
use crate::error::CommandError;
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Condvar, Mutex, MutexGuard, PoisonError};
use std::time::Duration;

pub type Reply = Result<Value, CommandError>;

pub struct JobSpec {
    pub id: String,
    pub image_id: String,
    pub bytes: Arc<Vec<u8>>,
    pub parameters: Value,
    pub auto: bool,
    /// A worker test hook as JSON; obeyed only where the worker's environment allows it.
    pub test: Option<Value>,
}

struct Job {
    spec: JobSpec,
    reply: mpsc::Sender<Reply>,
    on_start: Box<dyn FnOnce() + Send>,
}

struct Running {
    id: String,
    image_id: String,
    child: Arc<Mutex<Option<Child>>>,
    cancelled: Arc<AtomicBool>,
}

#[derive(Default)]
struct State {
    waiting: VecDeque<Job>,
    running: Option<Running>,
}

struct Shared {
    state: Mutex<State>,
    wake: Condvar,
    exe: PathBuf,
    env: Vec<(String, String)>,
}

#[derive(Clone)]
pub struct TraceQueue {
    shared: Arc<Shared>,
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

fn kill(r: &Running) {
    r.cancelled.store(true, Ordering::SeqCst);
    if let Some(child) = lock(&r.child).as_mut() {
        let _ = child.kill();
    }
}

fn refuse(job: &Job) {
    let _ = job.reply.send(Err(CommandError::cancelled()));
}

impl TraceQueue {
    /// A queue whose workers are `exe --trace-worker`.
    pub fn new(exe: PathBuf) -> TraceQueue {
        TraceQueue::with_env(exe, Vec::new())
    }

    /// The same, with variables added to each worker's environment (the tests' hooks).
    pub fn with_env(exe: PathBuf, env: Vec<(String, String)>) -> TraceQueue {
        let shared = Arc::new(Shared { state: Mutex::default(), wake: Condvar::new(), exe, env });
        let runner = shared.clone();
        std::thread::Builder::new().name("trace-queue".into()).spawn(move || run(&runner)).expect("the trace queue's thread starts");
        TraceQueue { shared }
    }

    /// Queue `spec`; `on_start` is called when its worker starts.
    pub fn submit(&self, spec: JobSpec, on_start: impl FnOnce() + Send + 'static) -> mpsc::Receiver<Reply> {
        let (tx, rx) = mpsc::channel();
        let mut st = lock(&self.shared.state);
        st.waiting.retain(|j| {
            let same = j.spec.image_id == spec.image_id;
            if same {
                refuse(j);
            }
            !same
        });
        if let Some(r) = st.running.as_ref().filter(|r| r.image_id == spec.image_id) {
            kill(r);
        }
        st.waiting.push_back(Job { spec, reply: tx, on_start: Box::new(on_start) });
        self.shared.wake.notify_one();
        rx
    }

    /// Cancel the job `id`, running or queued; false when there is no such job.
    pub fn cancel(&self, id: &str) -> bool {
        let mut st = lock(&self.shared.state);
        if let Some(r) = st.running.as_ref().filter(|r| r.id == id) {
            kill(r);
            return true;
        }
        let before = st.waiting.len();
        st.waiting.retain(|j| {
            let hit = j.spec.id == id;
            if hit {
                refuse(j);
            }
            !hit
        });
        st.waiting.len() != before
    }

    /// Kill the running worker and drop every queued job (the app is quitting).
    pub fn shutdown(&self) {
        let mut st = lock(&self.shared.state);
        st.waiting.drain(..).for_each(|j| refuse(&j));
        if let Some(r) = st.running.as_ref() {
            kill(r);
        }
    }
}

fn run(shared: &Shared) {
    loop {
        let (job, child, cancelled) = {
            let mut st = lock(&shared.state);
            let job = loop {
                if let Some(j) = st.waiting.pop_front() {
                    break j;
                }
                st = shared.wake.wait(st).unwrap_or_else(PoisonError::into_inner);
            };
            let child = Arc::new(Mutex::new(None));
            let cancelled = Arc::new(AtomicBool::new(false));
            st.running = Some(Running { id: job.spec.id.clone(), image_id: job.spec.image_id.clone(), child: child.clone(), cancelled: cancelled.clone() });
            (job, child, cancelled)
        };
        let Job { spec, reply, on_start } = job;
        let answer = trace(shared, spec, on_start, &child, &cancelled);
        lock(&shared.state).running = None;
        let _ = reply.send(answer);
    }
}

fn trace(shared: &Shared, spec: JobSpec, on_start: Box<dyn FnOnce() + Send>, slot: &Mutex<Option<Child>>, cancelled: &AtomicBool) -> Reply {
    let mut cmd = Command::new(&shared.exe);
    cmd.arg(crate::worker::FLAG).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    for (k, v) in &shared.env {
        cmd.env(k, v);
    }
    let mut child = cmd.spawn().map_err(|e| CommandError::crashed(format!("the worker did not start: {e}")))?;
    let (mut stdin, mut stdout, mut stderr) = (child.stdin.take().unwrap(), child.stdout.take().unwrap(), child.stderr.take().unwrap());
    *lock(slot) = Some(child);
    if cancelled.load(Ordering::SeqCst) {
        if let Some(c) = lock(slot).as_mut() {
            let _ = c.kill();
        }
    }
    on_start();

    let header = json!({ "parameters": spec.parameters, "auto": spec.auto, "bytes": spec.bytes.len(), "test": spec.test });
    let bytes = spec.bytes.clone();
    // written on its own thread: a worker that dies early must not leave this one blocked on a full pipe
    let writer = std::thread::spawn(move || -> std::io::Result<()> {
        writeln!(stdin, "{header}")?;
        stdin.write_all(&bytes)
    });
    let reader = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = stdout.read_to_string(&mut s);
        s
    });
    let errors = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = stderr.read_to_string(&mut s);
        s
    });
    let status = loop {
        let polled = lock(slot).as_mut().map(|c| c.try_wait());
        match polled {
            Some(Ok(Some(status))) => break Ok(status),
            Some(Err(e)) => break Err(e),
            _ => std::thread::sleep(Duration::from_millis(10)),
        }
    };
    let _ = writer.join();
    let out = reader.join().unwrap_or_default();
    let err_text = errors.join().unwrap_or_default();
    if cancelled.load(Ordering::SeqCst) {
        return Err(CommandError::cancelled());
    }
    parse(&out).unwrap_or_else(|| {
        let why = match status {
            Ok(s) => s.to_string(),
            Err(e) => e.to_string(),
        };
        if !err_text.trim().is_empty() {
            eprintln!("trace worker ({why}): {}", err_text.trim());
        }
        Err(CommandError::crashed(why))
    })
}

/// The worker's answer line; None when it wrote none that reads.
pub fn parse(out: &str) -> Option<Reply> {
    let v: Value = serde_json::from_str(out.lines().next()?).ok()?;
    if let Some(ok) = v.get("ok") {
        return Some(Ok(ok.clone()));
    }
    let err = v.get("err")?;
    Some(Err(CommandError { status: err["status"].as_u64()? as u16, body: err["body"].clone() }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_the_workers_line() {
        assert_eq!(parse("{\"ok\": {\"a\": 1}}\n").unwrap().unwrap(), json!({"a": 1}));
        let err = parse("{\"err\": {\"status\": 422, \"body\": {\"detail\": []}}}").unwrap().unwrap_err();
        assert_eq!((err.status, err.body), (422, json!({"detail": []})));
        assert!(parse("").is_none());
        assert!(parse("not json").is_none());
        assert!(parse("{\"neither\": 1}").is_none());
    }
}
