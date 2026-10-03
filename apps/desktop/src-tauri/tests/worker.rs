//! The worker and the queue on the real binary. The test hooks make a worker sleep, die or babble.
use serde_json::{json, Value};
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use studi0trace_desktop::queue::{JobSpec, TraceQueue};

const EXE: &str = env!("CARGO_BIN_EXE_studi0trace-desktop");
const WAIT: Duration = Duration::from_secs(120);

fn fixture(name: &str) -> Vec<u8> {
    std::fs::read(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../crates/studi0trace-core/tests/fixtures").join(name)).unwrap()
}

fn hooked() -> TraceQueue {
    TraceQueue::with_env(PathBuf::from(EXE), vec![("STUDI0TRACE_TEST_HOOKS".into(), "1".into())])
}

fn job(id: &str, image: &str, test: Option<Value>) -> JobSpec {
    JobSpec { id: id.into(), image_id: image.into(), bytes: Arc::new(fixture("intake_png.png")), parameters: json!({}), auto: false, test }
}

fn worker(header: Value, bytes: &[u8]) -> Value {
    let mut child = Command::new(EXE).arg("--trace-worker").stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap();
    let mut stdin = child.stdin.take().unwrap();
    writeln!(stdin, "{header}").unwrap();
    stdin.write_all(bytes).unwrap();
    drop(stdin);
    let out = child.wait_with_output().unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    serde_json::from_slice(&out.stdout).unwrap()
}

#[test]
fn the_worker_answers_as_the_core_does() {
    let png = fixture("intake_png.png");
    let answer = worker(json!({"parameters": {}, "auto": false, "bytes": png.len()}), &png);
    let core = studi0trace_core::api::Core::new();
    let id = core.upload(&png).unwrap()["image_id"].as_str().unwrap().to_string();
    let want = core.vectorize(&id, &json!({}), false).unwrap();
    assert_eq!(answer["ok"]["results"]["vexel"]["svg"], want["results"]["vexel"]["svg"]);
    assert_eq!(answer["ok"]["width"], want["width"]);
}

#[test]
fn a_refusal_comes_back_with_its_status_and_body() {
    let png = fixture("intake_png.png");
    let answer = worker(json!({"parameters": {"detail": "lots"}, "auto": false, "bytes": png.len()}), &png);
    assert_eq!(answer["err"]["status"], 422);
    assert!(answer["err"]["body"]["detail"].is_array(), "{answer}");
}

#[test]
fn one_worker_at_a_time_in_order() {
    let q = hooked();
    let started: Arc<Mutex<Vec<(&str, Instant)>>> = Arc::default();
    let (s1, s2) = (started.clone(), started.clone());
    let a = q.submit(job("a", "img-a", Some(json!({"sleep": {"ms": 600}}))), move || s1.lock().unwrap().push(("a", Instant::now())));
    let b = q.submit(job("b", "img-b", None), move || s2.lock().unwrap().push(("b", Instant::now())));
    assert!(a.recv_timeout(WAIT).unwrap().is_ok());
    assert!(b.recv_timeout(WAIT).unwrap().is_ok());
    let s = started.lock().unwrap();
    assert_eq!(s.iter().map(|x| x.0).collect::<Vec<_>>(), ["a", "b"]);
    assert!(s[1].1.duration_since(s[0].1) >= Duration::from_millis(600), "b started while a ran");
}

#[test]
fn a_newer_job_for_an_image_kills_the_running_one() {
    let q = hooked();
    let old = q.submit(job("old", "img", Some(json!({"sleep": {"ms": 30000}}))), || {});
    std::thread::sleep(Duration::from_millis(300));
    let t = Instant::now();
    let new = q.submit(job("new", "img", None), || {});
    assert_eq!(old.recv_timeout(WAIT).unwrap().unwrap_err().code(), Some("cancelled"));
    assert!(t.elapsed() < Duration::from_secs(3), "{:?}", t.elapsed());
    assert!(new.recv_timeout(WAIT).unwrap().is_ok());
}

#[test]
fn a_queued_job_for_the_same_image_is_replaced() {
    let q = hooked();
    let busy = q.submit(job("busy", "other", Some(json!({"sleep": {"ms": 1500}}))), || {});
    std::thread::sleep(Duration::from_millis(200));
    let first = q.submit(job("first", "img", None), || {});
    let second = q.submit(job("second", "img", None), || {});
    assert_eq!(first.recv_timeout(WAIT).unwrap().unwrap_err().code(), Some("cancelled"));
    assert!(busy.recv_timeout(WAIT).unwrap().is_ok());
    assert!(second.recv_timeout(WAIT).unwrap().is_ok());
}

#[test]
fn cancel_kills_a_running_job_and_drops_a_queued_one() {
    let q = hooked();
    let running = q.submit(job("r", "a", Some(json!({"sleep": {"ms": 30000}}))), || {});
    let queued = q.submit(job("q", "b", None), || {});
    std::thread::sleep(Duration::from_millis(300));
    assert!(q.cancel("q"));
    assert_eq!(queued.recv_timeout(WAIT).unwrap().unwrap_err().code(), Some("cancelled"));
    let t = Instant::now();
    assert!(q.cancel("r"));
    assert_eq!(running.recv_timeout(WAIT).unwrap().unwrap_err().code(), Some("cancelled"));
    assert!(t.elapsed() < Duration::from_secs(3), "{:?}", t.elapsed());
    assert!(!q.cancel("nope"));
}

#[test]
fn a_cancel_that_arrives_before_its_job_refuses_it_and_runs_no_worker() {
    let q = hooked();
    assert!(!q.cancel("x"));
    let started = Arc::new(Mutex::new(false));
    let flag = started.clone();
    let early = q.submit(job("x", "img", None), move || *flag.lock().unwrap() = true);
    assert_eq!(early.recv_timeout(WAIT).unwrap().unwrap_err().code(), Some("cancelled"));
    assert!(!*started.lock().unwrap(), "a worker ran");
    // the tombstone is consumed: the same id traces normally the next time
    let again = q.submit(job("x", "img", None), || {});
    assert!(again.recv_timeout(WAIT).unwrap().is_ok());
}

#[test]
fn a_worker_that_dies_or_babbles_is_a_crashed_trace() {
    let q = hooked();
    for hook in [json!("die"), json!("garbage")] {
        let r = q.submit(job("x", "img", Some(hook.clone())), || {}).recv_timeout(WAIT).unwrap();
        assert_eq!(r.unwrap_err().code(), Some("engine_crashed"), "{hook}");
    }
}

#[test]
fn hooks_are_ignored_without_the_variable() {
    let q = TraceQueue::new(PathBuf::from(EXE));
    let t = Instant::now();
    let r = q.submit(job("x", "img", Some(json!({"sleep": {"ms": 30000}}))), || {}).recv_timeout(WAIT).unwrap();
    assert!(r.is_ok() && t.elapsed() < Duration::from_secs(20));
}

#[test]
fn shutdown_kills_the_running_worker_and_the_queue() {
    let q = hooked();
    let running = q.submit(job("r", "a", Some(json!({"sleep": {"ms": 30000}}))), || {});
    let queued = q.submit(job("q", "b", None), || {});
    std::thread::sleep(Duration::from_millis(300));
    q.shutdown();
    assert_eq!(running.recv_timeout(WAIT).unwrap().unwrap_err().code(), Some("cancelled"));
    assert_eq!(queued.recv_timeout(WAIT).unwrap().unwrap_err().code(), Some("cancelled"));
}
