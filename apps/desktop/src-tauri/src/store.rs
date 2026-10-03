//! The images open in the window: the desktop's id (`intake::image_id`: the core's hash of the bytes, and of
//! the path too when there is one) to the file's bytes and where it came from. The bytes are the compressed file, a few megabytes at most, so every open image is kept until the UI
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

    /// Keep `image`; the same id again (the same file at the same path, or the same bytes without one) is the
    /// same entry. The same bytes at two paths are two ids, so each keeps its own folder.
    pub fn insert(&self, image: OpenImage) -> Opened {
        let mut map = self.lock();
        Opened::from(&*map.entry(image.id.clone()).or_insert(image))
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
        // the same id again: one entry, the first
        images.insert(image("a", Some("/pics/a.png")));
        assert_eq!(images.len(), 1);
        assert_eq!(images.get("a").unwrap().path, None);
        images.insert(image("b", Some("/pics/a.png")));
        assert_eq!(images.get("b").unwrap().path.as_deref(), Some(std::path::Path::new("/pics/a.png")));
        assert!(images.remove("b"));
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
