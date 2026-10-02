//! The images open in the window: the core's id (a hash of the file) to the file's bytes and where it came
//! from. The bytes are the compressed file, a few megabytes at most, so every open image is kept until the UI
//! closes it; a trace hands them to its worker.
use serde::Serialize;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

#[derive(Debug, Clone)]
pub struct OpenImage {
    pub id: String,
    pub name: String,
    pub path: Option<PathBuf>,
    pub width: u32,
    pub height: u32,
    pub format: String,
    pub bytes: Arc<Vec<u8>>,
}

/// What the UI is told about an image.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Opened {
    pub id: String,
    pub name: String,
    pub path: Option<String>,
    pub width: u32,
    pub height: u32,
    pub format: String,
}

impl From<&OpenImage> for Opened {
    fn from(i: &OpenImage) -> Self {
        Opened {
            id: i.id.clone(),
            name: i.name.clone(),
            path: i.path.as_ref().map(|p| p.display().to_string()),
            width: i.width,
            height: i.height,
            format: i.format.clone(),
        }
    }
}

#[derive(Default)]
pub struct Images {
    inner: Mutex<HashMap<String, OpenImage>>,
}

impl Images {
    fn lock(&self) -> std::sync::MutexGuard<'_, HashMap<String, OpenImage>> {
        self.inner.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// Keep `image`; the same file opened again is the same entry, and learns a path it did not have.
    pub fn insert(&self, image: OpenImage) -> Opened {
        let mut map = self.lock();
        let entry = map.entry(image.id.clone()).or_insert_with(|| image.clone());
        if entry.path.is_none() && image.path.is_some() {
            entry.path = image.path;
        }
        Opened::from(&*entry)
    }

    pub fn get(&self, id: &str) -> Option<OpenImage> {
        self.lock().get(id).cloned()
    }

    pub fn bytes(&self, id: &str) -> Option<Arc<Vec<u8>>> {
        self.lock().get(id).map(|i| i.bytes.clone())
    }

    pub fn remove(&self, id: &str) -> bool {
        self.lock().remove(id).is_some()
    }

    pub fn len(&self) -> usize {
        self.lock().len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn image(id: &str, path: Option<&str>) -> OpenImage {
        OpenImage { id: id.into(), name: "a.png".into(), path: path.map(PathBuf::from), width: 4, height: 2, format: "PNG".into(), bytes: Arc::new(vec![1, 2, 3]) }
    }

    #[test]
    fn holds_each_file_once_and_lets_it_go() {
        let images = Images::default();
        let opened = images.insert(image("a", None));
        assert_eq!((opened.id.as_str(), opened.width, opened.path.as_deref()), ("a", 4, None));
        // the same file again, now from a path: one entry, which learns the path
        images.insert(image("a", Some("/pics/a.png")));
        assert_eq!(images.len(), 1);
        assert_eq!(images.get("a").unwrap().path.as_deref(), Some(std::path::Path::new("/pics/a.png")));
        assert_eq!(images.bytes("a").unwrap().as_slice(), &[1, 2, 3]);
        assert!(images.remove("a"));
        assert!(!images.remove("a"));
        assert!(images.bytes("a").is_none());
    }

    #[test]
    fn opened_is_camel_case_json() {
        let v = serde_json::to_value(Opened::from(&image("a", Some("/p/a.png")))).unwrap();
        assert_eq!(v, serde_json::json!({"id": "a", "name": "a.png", "path": "/p/a.png", "width": 4, "height": 2, "format": "PNG"}));
    }
}
