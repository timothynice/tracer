//! The OpenAI API key, in the login keychain as a generic password: service `com.studi0.trace.openai`, account
//! `api-key`. Never in settings.json, never sent to the webview: the UI learns only whether one is stored. An
//! unsigned build is a new app to the keychain each time it is rebuilt, so macOS may ask once to let it read the
//! key ("Always Allow" answers it).
use crate::error::CommandError;

pub const SERVICE: &str = "com.studi0.trace.openai";
pub const ACCOUNT: &str = "api-key";
/// `errSecItemNotFound`.
#[cfg(target_os = "macos")]
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

/// The app's key, for the request's Authorization header only.
pub fn read_key() -> Option<String> {
    read_key_in(SERVICE)
}

/// A pasted key, trimmed: something, one token, at most 1024 characters. The words never repeat it.
pub fn check_key(key: &str) -> Result<&str, CommandError> {
    let key = key.trim();
    if key.is_empty() {
        return Err(CommandError::bad_request("Paste your OpenAI API key."));
    }
    if key.len() > 1024 || key.chars().any(|c| c.is_whitespace() || c.is_control()) {
        return Err(CommandError::bad_request("That does not look like an OpenAI API key."));
    }
    Ok(key)
}

fn refused(why: impl std::fmt::Display) -> CommandError {
    CommandError::new(500, "keychain", format!("The keychain refused: {why}"))
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
pub fn read_key_in(service: &str) -> Option<String> {
    security_framework::passwords::get_generic_password(service, ACCOUNT).ok().and_then(|bytes| String::from_utf8(bytes).ok()).filter(|k| !k.is_empty())
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
pub fn read_key_in(_service: &str) -> Option<String> {
    None
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
        assert_eq!(read_key_in(&service), None);
        set_key_in(&service, "sk-test-one").unwrap();
        assert!(has_key_in(&service));
        assert_eq!(read_key_in(&service).as_deref(), Some("sk-test-one"));
        set_key_in(&service, " sk-test-two ").unwrap();
        assert_eq!(read_key_in(&service).as_deref(), Some("sk-test-two"));
        delete_key_in(&service).unwrap();
        assert!(!has_key_in(&service));
        delete_key_in(&service).unwrap(); // removing nothing is not a failure
    }
}
