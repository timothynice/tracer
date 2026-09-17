/** SHA-256 of a file's bytes as hex. Used as the cache key for traces. */
export async function hashFile(file: Blob): Promise<string> {
  const buffer = await file.arrayBuffer();
  if (globalThis.crypto?.subtle) {
    const digest = await crypto.subtle.digest("SHA-256", buffer);
    return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
  }
  // Fallback for environments without WebCrypto (tests): FNV-1a over the bytes.
  let h = 0x811c9dc5;
  for (const byte of new Uint8Array(buffer)) {
    h ^= byte;
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `fnv-${h.toString(16)}-${buffer.byteLength}`;
}
