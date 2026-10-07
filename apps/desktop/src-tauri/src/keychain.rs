//! The OpenAI API key, in the login keychain as a generic password: service `com.studi0.trace.openai`, account
//! `api-key`. Never in settings.json, never sent to the webview: the UI learns only whether one is stored. An
//! unsigned build is a new app to the keychain each time it is rebuilt, so macOS may ask once to let it read the
//! key ("Always Allow" answers it).
use crate::error::CommandError;

pub const SERVICE: &str = "com.studi0.trace.openai";
pub const ACCOUNT: &str = "api-key";
/// `errSecItemNotFound`.
const NOT_FOUND: i32 = -25300;

/// Whether the app's key is stored (read without its data, so it never prompts).
pub fn has_key() -> bool {
    has_key_in(SERVICE)
}

/// Stores (or replaces) the app's key, trimmed and checked.
pub fn set_key(key: &str) -> Result<(), CommandError> {
    set_key_in(SERVICE, key)
}

/// Removes the app's key; removing none is not a failure.
pub fn delete_key() -> Result<(), CommandError> {
    delete_key_in(SERVICE)
}

/// The app's key, for the request's Authorization header only: `None` when none is stored, an error when macOS
/// would not hand it over (a denied prompt), which is not the same as having none.
pub fn read_key() -> Result<Option<String>, CommandError> {
    read_key_in(SERVICE)
}

/// A pasted key, trimmed: something, one token of printable ASCII, at most 1024 characters (a zero-width space
/// or a byte-order mark in a pasted key is not whitespace, and would only fail later as a header). The words never
/// repeat it.
pub fn check_key(key: &str) -> Result<&str, CommandError> {
    let key = key.trim();
    if key.is_empty() {
        return Err(CommandError::bad_request("Paste your OpenAI API key."));
    }
    if key.len() > 1024 || key.chars().any(|c| !c.is_ascii_graphic()) {
        return Err(CommandError::bad_request("That does not look like an OpenAI API key."));
    }
    Ok(key)
}

fn refused(why: impl std::fmt::Display) -> CommandError {
    CommandError::new(500, "keychain", format!("The keychain refused: {why}"))
}

/// What reading the key's data came to: the key, none (`errSecItemNotFound`, or stored empty or not as text), or
/// the keychain's refusal, in words that say what to do. The words never carry the OS's reason, which may name
/// the item.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn classify_read(read: Result<Vec<u8>, i32>) -> Result<Option<String>, CommandError> {
    match read {
        Ok(bytes) => Ok(String::from_utf8(bytes).ok().filter(|k| !k.is_empty())),
        Err(NOT_FOUND) => Ok(None),
        Err(_) => Err(CommandError::new(500, "keychain", "macOS did not let Studi0Trace read the key. Allow access in the prompt, or remove and add the key again in Settings.")),
    }
}

#[cfg(target_os = "macos")]
pub fn has_key_in(service: &str) -> bool {
    use security_framework::item::{ItemClass, ItemSearchOptions};
    ItemSearchOptions::new().class(ItemClass::generic_password()).service(service).account(ACCOUNT).load_attributes(true).limit(1i64).search().is_ok_and(|found| !found.is_empty())
}

#[cfg(target_os = "macos")]
pub fn set_key_in(service: &str, key: &str) -> Result<(), CommandError> {
    let key = check_key(key)?;
    security_framework::passwords::set_generic_password(service, ACCOUNT, key.as_bytes()).map_err(refused)
}

#[cfg(target_os = "macos")]
pub fn delete_key_in(service: &str) -> Result<(), CommandError> {
    match security_framework::passwords::delete_generic_password(service, ACCOUNT) {
        Err(e) if e.code() != NOT_FOUND => Err(refused(e)),
        _ => Ok(()),
    }
}

#[cfg(target_os = "macos")]
pub fn read_key_in(service: &str) -> Result<Option<String>, CommandError> {
    classify_read(security_framework::passwords::get_generic_password(service, ACCOUNT).map_err(|e| e.code()))
}

// The app is a Mac app; elsewhere (a Linux `cargo check`) there is no keychain and so no key.
#[cfg(not(target_os = "macos"))]
pub fn has_key_in(_service: &str) -> bool {
    false
}

#[cfg(not(target_os = "macos"))]
pub fn set_key_in(_service: &str, key: &str) -> Result<(), CommandError> {
    check_key(key)?;
    Err(refused("there is no keychain on this system"))
}

#[cfg(not(target_os = "macos"))]
pub fn delete_key_in(_service: &str) -> Result<(), CommandError> {
    Ok(())
}

#[cfg(not(target_os = "macos"))]
pub fn read_key_in(_service: &str) -> Result<Option<String>, CommandError> {
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_service_and_account_are_the_specs() {
        assert_eq!((SERVICE, ACCOUNT), ("com.studi0.trace.openai", "api-key"));
    }

    #[test]
    fn a_pasted_key_is_trimmed_and_checked() {
        assert_eq!(check_key("  sk-proj-abc123\n").unwrap(), "sk-proj-abc123");
        assert_eq!(check_key("   ").unwrap_err().code(), Some("bad_request"));
        assert_eq!(check_key("sk-one sk-two").unwrap_err().code(), Some("bad_request"));
        assert_eq!(check_key(&"k".repeat(1025)).unwrap_err().code(), Some("bad_request"));
        // the words never repeat what was pasted
        assert!(!check_key("sk-one sk-two").unwrap_err().message().contains("sk-"));
    }

    #[test]
    fn a_key_with_an_invisible_or_non_ascii_character_is_not_a_key() {
        for pasted in ["\u{200b}sk-abc", "sk-abc\u{feff}", "sk-\u{e9}abc", "sk-a\u{200b}bc"] {
            let e = check_key(pasted).unwrap_err();
            assert_eq!(e.code(), Some("bad_request"), "{pasted:?}");
            assert!(!e.message().contains("sk-"), "the words never repeat the input");
        }
        assert_eq!(check_key("sk-proj-AbC_123-xyz").unwrap(), "sk-proj-AbC_123-xyz");
    }

    #[test]
    fn only_a_missing_item_is_no_key_any_other_refusal_is_a_keychain_error() {
        assert_eq!(classify_read(Ok(b"sk-test".to_vec())).unwrap().as_deref(), Some("sk-test"));
        assert_eq!(classify_read(Ok(Vec::new())).unwrap(), None);
        assert_eq!(classify_read(Err(NOT_FOUND)).unwrap(), None);
        // errSecAuthFailed (-25293), errSecUserCanceled (-128), errSecInteractionNotAllowed (-25308)
        for code in [-25293, -128, -25308] {
            let e = classify_read(Err(code)).unwrap_err();
            assert_eq!((e.code(), e.status), (Some("keychain"), 500), "{code}");
            assert!(e.message().contains("Allow access in the prompt"), "{code}");
        }
    }

    /// Writes to the login keychain, so it runs only when asked: `STUDI0TRACE_TEST_KEYCHAIN=1` (CI has no login
    /// keychain). Under a test service of its own, never the app's.
    #[test]
    fn stores_reads_replaces_and_removes_a_key_in_a_test_service() {
        if std::env::var("STUDI0TRACE_TEST_KEYCHAIN").as_deref() != Ok("1") {
            eprintln!("skipped: STUDI0TRACE_TEST_KEYCHAIN=1 runs it against the login keychain");
            return;
        }
        let service = format!("com.studi0.trace.test.{}", std::process::id());
        // removes the test item when the test ends, an assertion failing included
        struct Cleanup<'a>(&'a str);
        impl Drop for Cleanup<'_> {
            fn drop(&mut self) {
                let _ = delete_key_in(self.0);
            }
        }
        let _cleanup = Cleanup(&service);
        delete_key_in(&service).unwrap();
        assert!(!has_key_in(&service));
        assert_eq!(read_key_in(&service).unwrap(), None);
        set_key_in(&service, "sk-test-one").unwrap();
        assert!(has_key_in(&service));
        assert_eq!(read_key_in(&service).unwrap().as_deref(), Some("sk-test-one"));
        set_key_in(&service, " sk-test-two ").unwrap();
        assert_eq!(read_key_in(&service).unwrap().as_deref(), Some("sk-test-two"));
        delete_key_in(&service).unwrap();
        assert!(!has_key_in(&service));
        delete_key_in(&service).unwrap(); // removing nothing is not a failure
    }
}
