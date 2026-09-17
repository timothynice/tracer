import { useCallback, useEffect, useRef, useState } from "react";

import { uploadImage } from "@/lib/api";
import { hashFile } from "@/lib/hash";

export interface UploadedImage {
  file: File;
  hash: string;
  imageId: string;
  width: number;
  height: number;
  format: string;
  previewUrl: string;
}

export interface UploadState {
  image: UploadedImage | null;
  uploading: boolean;
  error: Error | null;
  upload: (file: File) => Promise<UploadedImage | null>;
  /** Re-send the current file (after `image_expired`) and return the fresh id. */
  reupload: () => Promise<string | null>;
  clear: () => void;
}

export function useUpload(): UploadState {
  const [image, setImage] = useState<UploadedImage | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const controller = useRef<AbortController | null>(null);
  const current = useRef<UploadedImage | null>(null);

  useEffect(() => {
    current.current = image;
  }, [image]);

  useEffect(() => () => {
    controller.current?.abort();
    if (current.current) URL.revokeObjectURL(current.current.previewUrl);
  }, []);

  const clear = useCallback(() => {
    controller.current?.abort();
    setImage((prev) => {
      if (prev) URL.revokeObjectURL(prev.previewUrl);
      return null;
    });
    setError(null);
    setUploading(false);
  }, []);

  const upload = useCallback(async (file: File) => {
    controller.current?.abort();
    const ctl = new AbortController();
    controller.current = ctl;
    setUploading(true);
    setError(null);
    try {
      const [hash, res] = await Promise.all([hashFile(file), uploadImage(file, ctl.signal)]);
      if (ctl.signal.aborted) return null;
      const next: UploadedImage = {
        file,
        hash,
        imageId: res.image_id,
        width: res.width,
        height: res.height,
        format: res.format,
        previewUrl: URL.createObjectURL(file),
      };
      setImage((prev) => {
        if (prev) URL.revokeObjectURL(prev.previewUrl);
        return next;
      });
      return next;
    } catch (err) {
      if ((err as Error).name === "AbortError") return null;
      setError(err as Error);
      return null;
    } finally {
      if (controller.current === ctl) setUploading(false);
    }
  }, []);

  const reupload = useCallback(async () => {
    const img = current.current;
    if (!img) return null;
    const res = await uploadImage(img.file);
    const next = { ...img, imageId: res.image_id };
    setImage(next);
    return res.image_id;
  }, []);

  return { image, uploading, error, upload, reupload, clear };
}
