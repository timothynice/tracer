/** The sample images the empty state offers, served from `public/samples`. */
export const SAMPLES = [
  { name: "logo.png", label: "Logo" },
  { name: "sticker.png", label: "Flat art" },
  { name: "gradient.png", label: "Gradient" },
  { name: "shadow.png", label: "Shadow" },
] as const;

export const sampleUrl = (name: string) => `/samples/${name}`;

/** A sample, as a file to open. A missing sample is an error, not an empty image. */
export async function loadSample(name: string): Promise<File> {
  const res = await fetch(sampleUrl(name));
  if (!res.ok) throw new Error(`The sample ${name} could not be loaded (${res.status}).`);
  const blob = await res.blob();
  return new File([blob], name, { type: blob.type || "image/png" });
}
