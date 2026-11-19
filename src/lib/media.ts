export class MediaError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

export async function sha256Hex(data: Blob | ArrayBuffer): Promise<string> {
  const buf = data instanceof Blob ? await data.arrayBuffer() : data;
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

const OK_EXT = /\.(mp4|mov)$/i;
const OK_MIME = ["video/mp4", "video/quicktime"];

export function checkFormat(file: File) {
  if (!OK_EXT.test(file.name) && !OK_MIME.includes(file.type))
    throw new MediaError("UNSUPPORTED_FORMAT", `“${file.name}” is not an MP4 or MOV file.`);
}

/** Reads duration and frame size by letting the browser decode the file's metadata. */
export function probeVideo(url: string): Promise<{ durationMs: number; width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const v = document.createElement("video");
    v.preload = "metadata";
    v.muted = true;
    const done = () => {
      v.removeAttribute("src");
      v.load();
    };
    v.onloadedmetadata = () => {
      const d = v.duration;
      if (!Number.isFinite(d) || d <= 0) {
        done();
        reject(new MediaError("INVALID_MEDIA", "The video has no readable duration."));
        return;
      }
      resolve({ durationMs: Math.round(d * 1000), width: v.videoWidth, height: v.videoHeight });
      done();
    };
    v.onerror = () => {
      done();
      reject(new MediaError("INVALID_MEDIA", "This browser can't decode the video (it may be corrupt or use an unsupported codec such as HEVC)."));
    };
    v.src = url;
  });
}

export function formatBytes(n: number) {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
