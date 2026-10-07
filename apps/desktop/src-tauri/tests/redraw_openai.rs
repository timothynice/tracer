//! The OpenAI client and the redraw pipeline against a mock server on 127.0.0.1 (std's TcpListener, no new
//! crate): what is sent, what comes back, each failure's code, a slow reply abandoned. No test here calls OpenAI;
//! `redraw_live.rs` does, by hand.
use base64::Engine;
use serde_json::json;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use studi0trace_desktop::redraw::drift::Verdict;
use studi0trace_desktop::redraw::openai::{self, Api, EditRequest};
use studi0trace_desktop::redraw::{self, Model, Options, Phase, Quality, Source};

const KEY: &str = "sk-test-0123456789abcdef";
const BLUE: [u8; 3] = [0, 80, 200];

struct Reply {
    status: u16,
    body: Vec<u8>,
    delay: Duration,
}

impl Reply {
    fn json(status: u16, body: serde_json::Value) -> Reply {
        Reply { status, body: body.to_string().into_bytes(), delay: Duration::ZERO }
    }
    fn raw(status: u16, body: &[u8]) -> Reply {
        Reply { status, body: body.to_vec(), delay: Duration::ZERO }
    }
    fn after(self, delay: Duration) -> Reply {
        Reply { delay, ..self }
    }
}

/// One request the mock read: its head (request line and headers) and its body.
struct Seen {
    head: String,
    body: Vec<u8>,
}

struct Mock {
    base: String,
    seen: Arc<Mutex<Vec<Seen>>>,
}

fn read_line(stream: &mut TcpStream) -> String {
    let (mut line, mut byte) = (Vec::new(), [0u8; 1]);
    while !line.ends_with(b"\r\n") {
        if stream.read(&mut byte).unwrap_or(0) == 0 {
            break;
        }
        line.push(byte[0]);
    }
    String::from_utf8_lossy(&line).trim_end_matches("\r\n").to_string()
}

fn read_request(stream: &mut TcpStream) -> (String, Vec<u8>) {
    let mut head = String::new();
    loop {
        let line = read_line(stream);
        if line.is_empty() {
            break;
        }
        head.push_str(&line);
        head.push('\n');
    }
    let lower = head.to_ascii_lowercase();
    let header = |name: &str| lower.lines().find_map(|l| l.strip_prefix(name)).map(|v| v.trim().to_string());
    let body = if let Some(len) = header("content-length:").and_then(|v| v.parse::<usize>().ok()) {
        let mut body = vec![0u8; len];
        stream.read_exact(&mut body).unwrap();
        body
    } else if header("transfer-encoding:").is_some_and(|v| v.contains("chunked")) {
        let mut body = Vec::new();
        loop {
            let size = usize::from_str_radix(read_line(stream).split(';').next().unwrap_or("0").trim(), 16).unwrap_or(0);
            if size == 0 {
                read_line(stream);
                break;
            }
            let mut chunk = vec![0u8; size];
            stream.read_exact(&mut chunk).unwrap();
            body.extend_from_slice(&chunk);
            read_line(stream);
        }
        body
    } else {
        Vec::new()
    };
    (head, body)
}

/// A server that answers one connection per reply, in order.
fn serve(replies: Vec<Reply>) -> Mock {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let base = format!("http://{}/v1", listener.local_addr().unwrap());
    let seen: Arc<Mutex<Vec<Seen>>> = Arc::default();
    let log = seen.clone();
    std::thread::spawn(move || {
        for reply in replies {
            let Ok((mut stream, _)) = listener.accept() else { return };
            let (head, body) = read_request(&mut stream);
            log.lock().unwrap().push(Seen { head, body });
            std::thread::sleep(reply.delay);
            let _ = write!(stream, "HTTP/1.1 {} Mock\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", reply.status, reply.body.len());
            let _ = stream.write_all(&reply.body);
        }
    });
    Mock { base, seen }
}

fn png(w: u32, h: u32, colour: impl Fn(u32, u32) -> [u8; 3]) -> Vec<u8> {
    let mut out = std::io::Cursor::new(Vec::new());
    image::RgbImage::from_fn(w, h, |x, y| image::Rgb(colour(x, y))).write_to(&mut out, image::ImageFormat::Png).unwrap();
    out.into_inner()
}

fn image_reply(png: &[u8]) -> Reply {
    Reply::json(200, json!({ "created": 1, "data": [{ "b64_json": base64::engine::general_purpose::STANDARD.encode(png) }] }))
}

fn request(model: Model) -> EditRequest {
    EditRequest { png: png(8, 8, |_, _| [255, 255, 255]), width: 2048, height: 2048, model, quality: Quality::Medium }
}

/// A text field of a multipart body.
fn field(body: &[u8], name: &str) -> Option<String> {
    let text = String::from_utf8_lossy(body);
    let marker = format!("name=\"{name}\"\r\n\r\n");
    let start = text.find(&marker)? + marker.len();
    let end = text[start..].find("\r\n--")? + start;
    Some(text[start..end].to_string())
}

fn contains(hay: &[u8], needle: &[u8]) -> bool {
    hay.windows(needle.len()).any(|w| w == needle)
}

fn run<T>(f: impl std::future::Future<Output = T>) -> T {
    tauri::async_runtime::block_on(f)
}

fn bar(x: f64, y: f64) -> bool {
    (50.0..350.0).contains(&x) && (30.0..70.0).contains(&y)
}

#[test]
fn a_redraw_is_sent_as_the_spec_asks_and_comes_back_in_the_original_framing() {
    // 400 x 100 is padded to 400 x 134 (3:1), asked at 2048 x 688; the mock draws the padded source at that size
    let reply = png(2048, 688, |x, y| if bar((f64::from(x) + 0.5) * 400.0 / 2048.0, (f64::from(y) + 0.5) * 134.0 / 688.0 - 17.0) { BLUE } else { [255; 3] });
    let mock = serve(vec![image_reply(&reply)]);
    let rgba = (0..400u32 * 100)
        .flat_map(|i| -> [u8; 4] {
            let c = if bar(f64::from(i % 400) + 0.5, f64::from(i / 400) + 0.5) { BLUE } else { [255; 3] };
            [c[0], c[1], c[2], 255]
        })
        .collect();
    let source = Arc::new(Source { rgba, width: 400, height: 100 });
    let phases: Arc<Mutex<Vec<Phase>>> = Arc::default();
    let log = phases.clone();
    let api = Api::new(&mock.base, KEY).unwrap();
    let done = run(redraw::redraw(&api, source, Options { model: Model::GptImage2, quality: Quality::Medium }, Arc::new(move |p| log.lock().unwrap().push(p)))).unwrap();

    assert_eq!(*phases.lock().unwrap(), [Phase::Uploading, Phase::Drawing, Phase::Checking]);
    assert_eq!((done.width, done.height), (2048, 512));
    let back = image::load_from_memory(&done.png).unwrap();
    assert_eq!((back.width(), back.height()), (2048, 512));
    assert_eq!(done.drift.verdict, Verdict::Close, "{:?}", done.drift);

    let seen = mock.seen.lock().unwrap();
    let sent = &seen[0];
    assert!(sent.head.starts_with("POST /v1/images/edits HTTP/1.1"), "{}", sent.head);
    for (name, value) in [("model", "gpt-image-2"), ("prompt", openai::PROMPT), ("size", "2048x688"), ("quality", "medium"), ("output_format", "png"), ("background", "opaque"), ("n", "1")] {
        assert_eq!(field(&sent.body, name).as_deref(), Some(value), "{name}");
    }
    assert_eq!(field(&sent.body, "input_fidelity"), None);
    assert!(contains(&sent.body, b"name=\"image\"; filename=\"image.png\"\r\nContent-Type: image/png\r\n\r\n\x89PNG"));
    // the key is in the Authorization header and nowhere else
    let with_key: Vec<&str> = sent.head.lines().filter(|l| l.contains(KEY)).collect();
    assert_eq!(with_key.len(), 1, "{}", sent.head);
    assert_eq!(with_key[0].to_ascii_lowercase(), format!("authorization: bearer {}", KEY.to_ascii_lowercase()));
    assert!(!contains(&sent.body, KEY.as_bytes()));
}

#[test]
fn gpt_image_1_5_is_asked_for_high_input_fidelity_at_its_fixed_size() {
    let mock = serve(vec![image_reply(&png(4, 4, |_, _| [0; 3]))]);
    let api = Api::new(&mock.base, KEY).unwrap();
    let got = run(api.edit(EditRequest { width: 1536, height: 1024, ..request(Model::GptImage15) }, || {})).unwrap();
    assert!(got.starts_with(b"\x89PNG"));
    let seen = mock.seen.lock().unwrap();
    assert_eq!(field(&seen[0].body, "model").as_deref(), Some("gpt-image-1.5"));
    assert_eq!(field(&seen[0].body, "input_fidelity").as_deref(), Some("high"));
    assert_eq!(field(&seen[0].body, "size").as_deref(), Some("1536x1024"));
}

/// Nothing an error says, shown or debugged, holds the key (or the part of it OpenAI's own messages quote).
fn assert_no_key(err: &studi0trace_desktop::error::CommandError) {
    let words = format!("{} {} {:?}", serde_json::to_string(&err.body).unwrap(), err.message(), err);
    assert!(!words.contains("sk-test") && !words.contains(KEY) && !words.contains("0123456789abcdef"), "the key leaked: {words}");
}

/// The code `reply` comes back as; its words never hold the key.
fn code_of(reply: Reply) -> String {
    let mock = serve(vec![reply]);
    let api = Api::new(&mock.base, KEY).unwrap();
    let err = run(api.edit(request(Model::GptImage2), || {})).unwrap_err();
    assert_no_key(&err);
    err.code().unwrap().to_string()
}

#[test]
fn a_refused_key_is_invalid_key_and_its_words_never_quote_it() {
    assert_eq!(code_of(Reply::json(401, json!({"error": {"message": "Incorrect API key provided: sk-test-****cdef.", "type": "invalid_request_error", "code": "invalid_api_key"}}))), "invalid_key");
}

#[test]
fn a_rate_limit_and_an_empty_account_are_quota() {
    assert_eq!(code_of(Reply::json(429, json!({"error": {"message": "Rate limit reached", "type": "requests", "code": "rate_limit_exceeded"}}))), "quota");
    assert_eq!(code_of(Reply::json(429, json!({"error": {"message": "You exceeded your current quota", "type": "insufficient_quota", "code": "insufficient_quota"}}))), "quota");
}

#[test]
fn a_garbage_body_is_bad_reply() {
    assert_eq!(code_of(Reply::raw(200, b"<html>gateway</html>")), "bad_reply");
    assert_eq!(code_of(Reply::json(200, json!({"data": []}))), "bad_reply");
    assert_eq!(code_of(Reply::raw(502, b"Bad Gateway")), "bad_reply");
    // an image that is not one: the pipeline says so
    let mock = serve(vec![Reply::json(200, json!({"data": [{"b64_json": base64::engine::general_purpose::STANDARD.encode(b"not a png")}]}))]);
    let api = Api::new(&mock.base, KEY).unwrap();
    let source = Arc::new(Source { rgba: vec![255; 16 * 16 * 4], width: 16, height: 16 });
    let err = run(redraw::redraw(&api, source, Options { model: Model::GptImage2, quality: Quality::Medium }, Arc::new(|_| {}))).unwrap_err();
    assert_eq!(err.code(), Some("bad_reply"));
}

#[test]
fn a_slow_reply_is_abandoned_when_cancelled_and_never_read() {
    let mock = serve(vec![image_reply(&png(4, 4, |_, _| [0; 3])).after(Duration::from_secs(5))]);
    let api = Api::new(&mock.base, KEY).unwrap();
    let arrived = Arc::new(AtomicBool::new(false));
    let flag = arrived.clone();
    let task = tauri::async_runtime::spawn(async move {
        let answer = api.edit(request(Model::GptImage2), || {}).await;
        flag.store(true, Ordering::SeqCst);
        answer.is_ok()
    });
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(mock.seen.lock().unwrap().len(), 1, "the request reached the server");
    let started = Instant::now();
    task.abort();
    assert!(run(task).is_err(), "an abandoned request has no answer");
    assert!(started.elapsed() < Duration::from_secs(1), "the cancel waited for the reply");
    std::thread::sleep(Duration::from_millis(200));
    assert!(!arrived.load(Ordering::SeqCst), "the reply was read after the cancel");
}

#[test]
fn a_reply_slower_than_the_timeout_is_timeout() {
    let mock = serve(vec![image_reply(&png(4, 4, |_, _| [0; 3])).after(Duration::from_secs(3))]);
    let api = Api::with_timeout(&mock.base, KEY, Duration::from_millis(300)).unwrap();
    let err = run(api.edit(request(Model::GptImage2), || {})).unwrap_err();
    assert_no_key(&err);
    assert_eq!(err.code(), Some("timeout"));
}

#[test]
fn nothing_listening_is_offline() {
    // bound and dropped: nothing listens there now
    let port = TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port();
    let api = Api::new(&format!("http://127.0.0.1:{port}/v1"), KEY).unwrap();
    let err = run(api.edit(request(Model::GptImage2), || {})).unwrap_err();
    assert_no_key(&err);
    assert_eq!(err.code(), Some("offline"));
}

#[test]
fn the_base_url_is_the_environments_when_set() {
    std::env::set_var(openai::BASE_ENV, "http://127.0.0.1:9/v1/");
    assert_eq!(openai::base_url(), "http://127.0.0.1:9/v1");
    std::env::remove_var(openai::BASE_ENV);
    assert_eq!(openai::base_url(), openai::DEFAULT_BASE);
}
