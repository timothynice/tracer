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
    /// While this image is drawn from an AI redraw: the id of the entry that keeps the original's bytes.
    pub original: Option<String>,
}

/// The id of the entry that keeps an image's original while its source is a redraw.
pub fn original_id(id: &str) -> String {
    format!("{id}-original")
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

    /// Lets an image go, and its original with it when it is drawn from a redraw.
    pub fn remove(&self, id: &str) -> bool {
        let mut map = self.lock();
        let Some(gone) = map.remove(id) else { return false };
        if let Some(original) = gone.original {
            map.remove(&original);
        }
        true
    }

    /// The image as it was opened: its original when it is drawn from a redraw, else itself.
    pub fn source_of(&self, id: &str) -> Option<OpenImage> {
        let map = self.lock();
        let image = map.get(id)?;
        match &image.original {
            Some(original) => map.get(original).cloned(),
            None => Some(image.clone()),
        }
    }

    /// Use redraw: the image keeps its id and takes the redraw's bytes, size and format; its original moves to
    /// [`original_id`] (a second redraw keeps the first original); the redraw's own entry goes. Answers the image
    /// and its original, or None (and changes nothing) when either is unknown.
    pub fn accept_redraw(&self, id: &str, redraw_id: &str) -> Option<(Opened, Opened)> {
        let mut map = self.lock();
        let redraw = map.get(redraw_id)?.clone();
        let image = map.get(id)?.clone();
        let key = match image.original.clone() {
            Some(original) => original,
            None => {
                let key = original_id(id);
                map.insert(key.clone(), OpenImage { id: key.clone(), original: None, ..image });
                key
            }
        };
        map.remove(redraw_id);
        let entry = map.get_mut(id)?;
        entry.bytes = redraw.bytes;
        entry.width = redraw.width;
        entry.height = redraw.height;
        entry.format = redraw.format;
        entry.original = Some(key.clone());
        let shown = Opened::from(&*entry);
        Some((shown, Opened::from(map.get(&key)?)))
    }

    /// Revert to Original: the image takes its original's bytes back and the original's entry goes. None when the
    /// image is not drawn from a redraw.
    pub fn revert(&self, id: &str) -> Option<Opened> {
        let mut map = self.lock();
        let key = map.get(id)?.original.clone()?;
        let original = map.remove(&key)?;
        let entry = map.get_mut(id)?;
        entry.bytes = original.bytes;
        entry.width = original.width;
        entry.height = original.height;
        entry.format = original.format;
        entry.original = None;
        Some(Opened::from(&*entry))
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
        OpenImage { id: id.into(), name: "a.png".into(), path: path.map(PathBuf::from), width: 4, height: 2, format: "PNG".into(), bytes: Arc::new(vec![1, 2, 3]), original: None }
    }

    fn sized(id: &str, side: u32, bytes: &[u8]) -> OpenImage {
        OpenImage { id: id.into(), name: "logo.png".into(), path: Some(PathBuf::from("/pics/logo.png")), width: side, height: side, format: "PNG".into(), bytes: Arc::new(bytes.to_vec()), original: None }
    }

    #[test]
    fn using_a_redraw_swaps_the_bytes_and_keeps_the_original() {
        let images = Images::default();
        images.insert(sized("a", 64, &[1]));
        images.insert(OpenImage { path: None, ..sized("r", 2048, &[2]) });
        let (shown, original) = images.accept_redraw("a", "r").unwrap();
        assert_eq!((shown.id.as_str(), shown.width, shown.path.as_deref()), ("a", 2048, Some("/pics/logo.png")));
        assert_eq!((original.id, original.width), (original_id("a"), 64));
        assert_eq!(images.bytes("a").unwrap().as_slice(), &[2]);
        assert_eq!(images.bytes(&original_id("a")).unwrap().as_slice(), &[1]);
        assert!(images.get("r").is_none(), "the redraw's own entry is the image's now");
        assert_eq!(images.source_of("a").unwrap().bytes.as_slice(), &[1], "a redraw is made from the original");
        // a second redraw replaces the first; the original stays the one opened
        images.insert(OpenImage { path: None, ..sized("r2", 1024, &[3]) });
        let (_, original) = images.accept_redraw("a", "r2").unwrap();
        assert_eq!(original.width, 64);
        assert_eq!(images.bytes("a").unwrap().as_slice(), &[3]);
        assert_eq!(images.len(), 2);
    }

    #[test]
    fn revert_puts_the_original_back_and_closing_lets_both_go() {
        let images = Images::default();
        images.insert(sized("a", 64, &[1]));
        images.insert(sized("r", 2048, &[2]));
        images.accept_redraw("a", "r").unwrap();
        let back = images.revert("a").unwrap();
        assert_eq!((back.id.as_str(), back.width), ("a", 64));
        assert_eq!(images.bytes("a").unwrap().as_slice(), &[1]);
        assert!(images.get(&original_id("a")).is_none());
        assert_eq!(images.get("a").unwrap().original, None);
        assert_eq!(images.revert("a"), None, "an image that is not a redraw has nothing to revert");
        images.insert(sized("r", 2048, &[2]));
        images.accept_redraw("a", "r").unwrap();
        assert!(images.remove("a"));
        assert!(images.is_empty(), "closing an image lets its original go too");
    }

    #[test]
    fn nothing_to_use_changes_nothing() {
        let images = Images::default();
        images.insert(sized("a", 64, &[1]));
        assert_eq!(images.accept_redraw("a", "missing"), None);
        assert_eq!(images.accept_redraw("missing", "a"), None);
        assert_eq!(images.bytes("a").unwrap().as_slice(), &[1]);
        assert_eq!(images.len(), 1);
        assert_eq!(images.source_of("a").unwrap().id, "a");
        assert!(images.source_of("missing").is_none());
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
