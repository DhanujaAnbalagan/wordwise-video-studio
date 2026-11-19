/**
 * Real in-browser export: plays the original video through the render plan into a canvas
 * (captions burned in) and Web Audio graph (mute / 1 kHz bleep), recorded with MediaRecorder.
 * Runs in real time. The recorder is paused across every seek, so cut points add no time.
 */
import type { RenderPlan } from "@/engine/render";

export interface RenderOutput {
  blob: Blob;
  mime: string;
  measuredDurationMs: number;
  expectedDurationMs: number;
  wallMs: number;
  width: number;
  height: number;
}

const SIZES: Record<string, number> = { sm: 0.045, md: 0.06, lg: 0.08, xl: 0.1 };

function pickMime() {
  const c = ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm", "video/mp4"];
  return c.find((m) => typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(m)) ?? "";
}

function once(el: HTMLMediaElement, ev: string) {
  return new Promise<void>((res) => el.addEventListener(ev, () => res(), { once: true }));
}

export async function measureDuration(blob: Blob): Promise<number> {
  const v = document.createElement("video");
  v.preload = "metadata";
  v.src = URL.createObjectURL(blob);
  await once(v, "loadedmetadata");
  if (!Number.isFinite(v.duration)) {
    // MediaRecorder WebM files omit duration; seeking far forward forces the browser to compute it.
    v.currentTime = 1e7;
    await once(v, "durationchange");
    await new Promise((r) => setTimeout(r, 50));
  }
  const d = v.duration * 1000;
  URL.revokeObjectURL(v.src);
  return d;
}

export async function renderInBrowser(
  srcUrl: string,
  plan: RenderPlan,
  onProgress: (pct: number, label: string) => void,
  signal: AbortSignal,
): Promise<RenderOutput> {
  const mime = pickMime();
  if (!mime) throw new Error("This browser cannot record video (MediaRecorder unsupported).");
  const started = performance.now();
  const video = document.createElement("video");
  video.src = srcUrl;
  video.preload = "auto";
  video.playsInline = true;
  video.crossOrigin = "anonymous";
  await once(video, "loadedmetadata");
  const W = video.videoWidth || 1280;
  const H = video.videoHeight || 720;
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const g = canvas.getContext("2d")!;

  const ac = new AudioContext();
  await ac.resume();
  const dest = ac.createMediaStreamDestination();
  const srcNode = ac.createMediaElementSource(video);
  const voice = ac.createGain();
  srcNode.connect(voice).connect(dest);
  const osc = ac.createOscillator();
  osc.frequency.value = 1000;
  const beep = ac.createGain();
  beep.gain.value = 0;
  osc.connect(beep).connect(dest);
  osc.start();

  const stream = new MediaStream([...canvas.captureStream(30).getVideoTracks(), ...dest.stream.getAudioTracks()]);
  const rec = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 4_000_000 });
  const chunks: Blob[] = [];
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  const stopped = new Promise<void>((r) => (rec.onstop = () => r()));

  let tlNow = 0;
  const draw = () => {
    g.drawImage(video, 0, 0, W, H);
    for (const c of plan.captions) {
      if (tlNow < c.tlStart || tlNow >= c.tlEnd) continue;
      const fs = Math.round(H * (SIZES[c.size] ?? 0.06));
      g.font = `600 ${fs}px "IBM Plex Sans", sans-serif`;
      const tw = g.measureText(c.text).width;
      const y = c.position === "top" ? H * 0.06 : H - H * 0.08 - fs;
      g.fillStyle = "rgba(10,12,16,0.78)";
      g.fillRect((W - tw) / 2 - fs * 0.4, y - fs * 0.25, tw + fs * 0.8, fs * 1.5);
      g.fillStyle = "#f3f4f6";
      g.textBaseline = "top";
      g.fillText(c.text, (W - tw) / 2, y);
    }
  };
  const applyAudio = () => {
    const active = plan.audio.filter((a) => tlNow >= a.tlStart && tlNow < a.tlEnd);
    voice.gain.value = active.length ? 0 : 1;
    beep.gain.value = active.some((a) => a.kind === "bleep") ? 0.2 : 0;
  };

  try {
    rec.start(250);
    rec.pause();
    for (let i = 0; i < plan.segments.length; i++) {
      if (signal.aborted) throw new DOMException("Render cancelled", "AbortError");
      const s = plan.segments[i];
      video.currentTime = s.srcStart / 1000;
      await once(video, "seeked");
      video.playbackRate = s.speed;
      tlNow = s.tlStart;
      applyAudio();
      draw();
      await video.play();
      rec.resume();
      await new Promise<void>((resolve, reject) => {
        const step = () => {
          if (signal.aborted) return reject(new DOMException("Render cancelled", "AbortError"));
          const src = video.currentTime * 1000;
          tlNow = s.tlStart + (Math.min(src, s.srcEnd) - s.srcStart) / s.speed;
          applyAudio();
          draw();
          onProgress(Math.min(99, (tlNow / plan.expectedDurationMs) * 100), `Segment ${i + 1}/${plan.segments.length}`);
          if (src >= s.srcEnd - 5 || video.ended) resolve();
          else requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
      });
      rec.pause();
      video.pause();
    }
    rec.resume();
    rec.stop();
    await stopped;
  } finally {
    stream.getTracks().forEach((t) => t.stop());
    osc.stop();
    void ac.close();
    video.removeAttribute("src");
  }
  const blob = new Blob(chunks, { type: mime.split(";")[0] });
  onProgress(100, "Verifying output");
  const measured = await measureDuration(blob);
  return { blob, mime, measuredDurationMs: measured, expectedDurationMs: plan.expectedDurationMs, wallMs: performance.now() - started, width: W, height: H };
}
