//! The image edit request to OpenAI and its reply. The base URL is `STUDI0TRACE_OPENAI_BASE` when it is set (the
//! tests' mock server), else OpenAI's. The key goes in the Authorization header and nowhere else: not in a message,
//! a log line or a `Debug`.

use super::{error, Model, Quality};
use crate::error::CommandError;
use std::time::Duration;

pub const DEFAULT_BASE: &str = "https://api.openai.com/v1";
pub const BASE_ENV: &str = "STUDI0TRACE_OPENAI_BASE";
pub const TIMEOUT: Duration = Duration::from_secs(120);
/// OpenAI's limit on a request; the intake's 20 MB cap keeps every source under it.
pub const MAX_REQUEST_BYTES: usize = 50 * 1024 * 1024;
/// The one prompt, for both models.
pub const PROMPT: &str = "Redraw this exact image at high resolution as clean flat artwork. Keep every shape, letter, proportion, spacing, position and colour exactly as in the input; do not add, remove, restyle or re-letter anything. Remove blur, compression noise, halos and pixelation. Keep the background a plain flat colour and keep the same framing and margins.";
/// The image is streamed in pieces this size, so the last one says the upload has gone.
const UPLOAD_CHUNK: usize = 64 * 1024;

/// `STUDI0TRACE_OPENAI_BASE` when set (without a trailing slash), else OpenAI's.
pub fn base_url() -> String {
    std::env::var(BASE_ENV).ok().map(|b| b.trim().trim_end_matches('/').to_string()).filter(|b| !b.is_empty()).unwrap_or_else(|| DEFAULT_BASE.to_string())
}

/// One image edit: the padded source as PNG and the size the model is asked for.
pub struct EditRequest {
    pub png: Vec<u8>,
    pub width: u32,
    pub height: u32,
    pub model: Model,
    pub quality: Quality,
}

/// The form's text fields, in the order they are sent (the image goes last).
pub fn form_fields(req: &EditRequest) -> Vec<(&'static str, String)> {
    let mut fields = vec![
        ("model", req.model.id().to_string()),
        ("prompt", PROMPT.to_string()),
        ("size", format!("{}x{}", req.width, req.height)),
        ("quality", req.quality.id().to_string()),
        ("output_format", "png".to_string()),
        ("background", "opaque".to_string()),
        ("n", "1".to_string()),
    ];
    // the parameter exists only for the 1.x models
    if req.model == Model::GptImage15 {
        fields.push(("input_fidelity", "high".to_string()));
    }
    fields
}

/// The reply's image bytes, or the failure's code. OpenAI's own message is never kept: a 401's quotes part of the
/// key. Only the status and OpenAI's error code and type (identifiers, no key) reach stderr.
pub fn parse_reply(status: u16, body: &[u8]) -> Result<Vec<u8>, CommandError> {
    use base64::Engine;
    let json: serde_json::Value = serde_json::from_slice(body).unwrap_or(serde_json::Value::Null);
    if (200..300).contains(&status) {
        let png = json["data"][0]["b64_json"].as_str().and_then(|b| base64::engine::general_purpose::STANDARD.decode(b).ok());
        return png.filter(|p| !p.is_empty()).ok_or_else(|| error("bad_reply"));
    }
    let code = json["error"]["code"].as_str().unwrap_or_default();
    let kind = json["error"]["type"].as_str().unwrap_or_default();
    eprintln!("studi0trace: OpenAI answered {status} (code {code:?}, type {kind:?})");
    Err(match (status, code) {
        (429, _) | (_, "insufficient_quota" | "billing_hard_limit_reached" | "rate_limit_exceeded") => error("quota"),
        (401, _) | (_, "invalid_api_key") => error("invalid_key"),
        (_, "moderation_blocked" | "content_policy_violation") => error("refused"),
        (413, _) => error("too_large"),
        _ => error("bad_reply"),
    })
}

/// What a request that never got its answer means to the user.
fn transport(e: &reqwest::Error) -> CommandError {
    if e.is_timeout() {
        error("timeout")
    } else if e.is_connect() {
        error("offline")
    } else {
        // reqwest's words name the URL, never the headers
        eprintln!("studi0trace: the request to OpenAI failed: {e}");
        if e.is_body() || e.is_decode() {
            error("bad_reply")
        } else {
            error("offline")
        }
    }
}

fn is_loopback(base: &str) -> bool {
    ["http://127.0.0.1", "http://localhost", "http://[::1]"].iter().any(|p| base.starts_with(p))
}

/// The PNG as a stream of pieces; `on_sent` runs as the last is handed to the connection.
fn upload_body(png: Vec<u8>, on_sent: Box<dyn FnOnce() + Send>) -> reqwest::Body {
    let chunks: Vec<Vec<u8>> = png.chunks(UPLOAD_CHUNK).map(<[u8]>::to_vec).collect();
    let last = chunks.len();
    let mut on_sent = Some(on_sent);
    reqwest::Body::wrap_stream(futures_util::stream::iter(chunks.into_iter().enumerate().map(move |(i, chunk)| {
        if i + 1 == last {
            if let Some(sent) = on_sent.take() {
                sent();
            }
        }
        Ok::<_, std::io::Error>(chunk)
    })))
}

/// A client for one base URL and one key.
pub struct Api {
    client: reqwest::Client,
    base: String,
    key: String,
}

impl std::fmt::Debug for Api {
    // the key is never printed
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Api {{ base: {:?} }}", self.base)
    }
}

impl Api {
    pub fn new(base: &str, key: &str) -> Result<Api, CommandError> {
        Api::with_timeout(base, key, TIMEOUT)
    }

    pub fn with_timeout(base: &str, key: &str, timeout: Duration) -> Result<Api, CommandError> {
        if rustls::crypto::CryptoProvider::get_default().is_none() {
            // the provider tauri-plugin-updater installs too; whichever comes first stays
            let _ = rustls::crypto::ring::default_provider().install_default();
        }
        let base = base.trim().trim_end_matches('/').to_string();
        let mut builder = reqwest::Client::builder().timeout(timeout).connect_timeout(Duration::from_secs(15));
        if is_loopback(&base) {
            // the tests' mock server: never through a system proxy
            builder = builder.no_proxy();
        }
        let client = builder.build().map_err(|e| {
            eprintln!("studi0trace: the HTTP client did not start: {e}");
            error("offline")
        })?;
        Ok(Api { client, base, key: key.to_string() })
    }

    /// Sends one image edit and answers the reply's image bytes. `on_sent` runs when the upload has gone.
    pub async fn edit(&self, req: EditRequest, on_sent: impl FnOnce() + Send + 'static) -> Result<Vec<u8>, CommandError> {
        if req.png.len() > MAX_REQUEST_BYTES {
            return Err(error("too_large"));
        }
        let mut form = reqwest::multipart::Form::new();
        for (name, value) in form_fields(&req) {
            form = form.text(name, value);
        }
        let len = req.png.len() as u64;
        let image = reqwest::multipart::Part::stream_with_length(upload_body(req.png, Box::new(on_sent)), len).file_name("image.png").mime_str("image/png").map_err(|_| error("bad_reply"))?;
        let form = form.part("image", image);
        let answer = self.client.post(format!("{}/images/edits", self.base)).bearer_auth(&self.key).multipart(form).send().await.map_err(|e| transport(&e))?;
        let status = answer.status().as_u16();
        let body = answer.bytes().await.map_err(|e| transport(&e))?;
        parse_reply(status, &body)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::redraw::{Model, Quality};
    use base64::Engine;

    fn req(model: Model) -> EditRequest {
        EditRequest { png: vec![1, 2, 3], width: 2048, height: 688, model, quality: Quality::High }
    }

    #[test]
    fn the_form_is_the_specs_and_only_1_5_asks_for_input_fidelity() {
        let two = form_fields(&req(Model::GptImage2));
        assert_eq!(
            two,
            vec![
                ("model", "gpt-image-2".to_string()),
                ("prompt", PROMPT.to_string()),
                ("size", "2048x688".to_string()),
                ("quality", "high".to_string()),
                ("output_format", "png".to_string()),
                ("background", "opaque".to_string()),
                ("n", "1".to_string()),
            ]
        );
        let one_five = form_fields(&req(Model::GptImage15));
        assert_eq!(one_five[0], ("model", "gpt-image-1.5".to_string()));
        assert_eq!(one_five.last(), Some(&("input_fidelity", "high".to_string())));
        assert!(PROMPT.starts_with("Redraw this exact image at high resolution as clean flat artwork.") && PROMPT.ends_with("keep the same framing and margins."));
    }

    #[test]
    fn a_reply_with_an_image_is_its_bytes() {
        let b64 = base64::engine::general_purpose::STANDARD.encode(b"\x89PNG-bytes");
        let body = serde_json::json!({"created": 1, "data": [{"b64_json": b64}]}).to_string();
        assert_eq!(parse_reply(200, body.as_bytes()).unwrap(), b"\x89PNG-bytes");
    }

    #[test]
    fn every_failure_has_its_code_and_never_quotes_openai() {
        let body = |code: &str, kind: &str| serde_json::json!({"error": {"message": "Incorrect API key provided: sk-abc***xyz", "code": code, "type": kind}}).to_string();
        let cases = [
            (200, "not json".to_string(), "bad_reply"),
            (200, serde_json::json!({"data": []}).to_string(), "bad_reply"),
            (200, serde_json::json!({"data": [{"b64_json": "%%%"}]}).to_string(), "bad_reply"),
            (200, serde_json::json!({"data": [{"b64_json": ""}]}).to_string(), "bad_reply"),
            (401, body("invalid_api_key", "invalid_request_error"), "invalid_key"),
            (429, body("rate_limit_exceeded", "requests"), "quota"),
            (429, body("insufficient_quota", "insufficient_quota"), "quota"),
            (400, body("billing_hard_limit_reached", "invalid_request_error"), "quota"),
            (400, body("moderation_blocked", "image_generation_user_error"), "refused"),
            (400, body("content_policy_violation", "invalid_request_error"), "refused"),
            (413, "".to_string(), "too_large"),
            (500, "upstream".to_string(), "bad_reply"),
            (400, body("invalid_value", "invalid_request_error"), "bad_reply"),
        ];
        for (status, text, code) in cases {
            let e = parse_reply(status, text.as_bytes()).unwrap_err();
            assert_eq!(e.code(), Some(code), "{status} {text}");
            assert!(!serde_json::to_string(&e.body).unwrap().contains("sk-"), "{status}: {:?}", e.body);
        }
    }

    #[test]
    fn the_default_base_is_openai_and_an_api_never_prints_its_key() {
        assert_eq!(DEFAULT_BASE, "https://api.openai.com/v1");
        assert_eq!(BASE_ENV, "STUDI0TRACE_OPENAI_BASE");
        assert_eq!(TIMEOUT, std::time::Duration::from_secs(120));
        let api = Api::new("http://127.0.0.1:9/v1/", "sk-secret-123").unwrap();
        let shown = format!("{api:?}");
        assert!(!shown.contains("sk-secret") && shown.contains("http://127.0.0.1:9/v1"), "{shown}");
    }
}
