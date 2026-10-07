//! AI redraw: an opt-in redraw of a rough image by OpenAI's image model, made with the user's own API key and
//! checked against the original before anything is traced from it (spec
//! `docs/superpowers/specs/2026-10-06-ai-redraw-design.md`). With `keychain.rs`, the only code in the app that
//! touches the network or the key. One responsibility per file: `geometry` (padding, sizes, the crop back),
//! `rough` (the inspector's hint), `drift` (how far a redraw moved the image), `openai` (the request and its
//! reply); this file holds the error words, the pipeline and the jobs in flight.
use crate::error::CommandError;

pub mod geometry;

/// An image whose longest side is under this many pixels looks rough.
pub const ROUGH_SIDE: u32 = 600;
/// The long side of the size `gpt-image-2` is asked for.
pub const REQUEST_SIDE: u32 = 2048;

/// The image model asked (Settings ▸ AI redraw ▸ Model).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Model {
    GptImage2,
    GptImage15,
}

impl Model {
    /// The setting's value; anything else is the default, `gpt-image-2`.
    pub fn parse(id: &str) -> Model {
        match id {
            "gpt-image-1.5" => Model::GptImage15,
            _ => Model::GptImage2,
        }
    }

    pub fn id(self) -> &'static str {
        match self {
            Model::GptImage2 => "gpt-image-2",
            Model::GptImage15 => "gpt-image-1.5",
        }
    }
}

/// Settings ▸ AI redraw ▸ Quality.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Quality {
    Medium,
    High,
}

impl Quality {
    /// The setting's value; anything else is the default, `medium`.
    pub fn parse(id: &str) -> Quality {
        match id {
            "high" => Quality::High,
            _ => Quality::Medium,
        }
    }

    pub fn id(self) -> &'static str {
        match self {
            Quality::Medium => "medium",
            Quality::High => "high",
        }
    }
}

/// A redraw's failure, in words for a person: never the key, and never OpenAI's own message (a 401's quotes part
/// of it). Every one leaves the image exactly as it was.
pub fn error(code: &'static str) -> CommandError {
    let (status, message) = match code {
        "no_key" => (401, "Add your OpenAI API key to use AI redraw."),
        "invalid_key" => (401, "OpenAI did not accept your API key. Replace it in Settings ▸ AI redraw."),
        "quota" => (429, "Your OpenAI account is out of credit or rate limited."),
        "refused" => (422, "OpenAI declined to redraw this image under its content policy."),
        "timeout" => (504, "OpenAI did not answer within 2 minutes. Try again."),
        "offline" => (503, "Studi0Trace could not reach OpenAI. Check your internet connection."),
        "bad_reply" => (502, "OpenAI's reply held no usable image. Try again."),
        "too_large" => (413, "This image is too large to send to OpenAI (50 MB at most)."),
        "cancelled" => (499, "The redraw was cancelled"),
        _ => (500, "The redraw failed."),
    };
    CommandError::new(status, code, message)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn models_and_qualities_read_their_setting_and_default_safely() {
        assert_eq!(Model::parse("gpt-image-1.5"), Model::GptImage15);
        assert_eq!(Model::parse("gpt-image-2"), Model::GptImage2);
        assert_eq!(Model::parse("dall-e-3"), Model::GptImage2);
        assert_eq!((Model::GptImage2.id(), Model::GptImage15.id()), ("gpt-image-2", "gpt-image-1.5"));
        assert_eq!(Quality::parse("high"), Quality::High);
        assert_eq!(Quality::parse(""), Quality::Medium);
        assert_eq!((Quality::Medium.id(), Quality::High.id()), ("medium", "high"));
        assert_eq!((ROUGH_SIDE, REQUEST_SIDE), (600, 2048));
    }

    #[test]
    fn every_failure_has_its_code_status_and_plain_words() {
        let table = [
            ("no_key", 401),
            ("invalid_key", 401),
            ("quota", 429),
            ("refused", 422),
            ("timeout", 504),
            ("offline", 503),
            ("bad_reply", 502),
            ("too_large", 413),
            ("cancelled", 499),
        ];
        for (code, status) in table {
            let e = error(code);
            assert_eq!((e.code(), e.status), (Some(code), status), "{code}");
            assert!(!e.message().is_empty() && !e.message().contains("sk-"), "{code}");
        }
        assert!(error("quota").message().to_lowercase().contains("your openai account is out of credit or rate limited"));
    }
}
