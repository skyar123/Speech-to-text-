/**
 * A minimal Edge neural-voice client for Node, ported from the Python
 * `edge-tts` package so the hosted endpoint has no Python runtime.
 *
 * Microsoft's read-aloud service speaks a small WebSocket protocol:
 *   1. connect with a time-derived `Sec-MS-GEC` token (without it: HTTP 403)
 *   2. send a `speech.config` text frame describing the desired output
 *   3. send an `ssml` text frame carrying the voice, prosody and text
 *   4. read binary frames — each is [2-byte header length][headers][audio] —
 *      plus `audio.metadata` text frames holding word/sentence timings
 *   5. stop at `turn.end`
 */

import { createHash, randomUUID } from "node:crypto";
import WebSocket from "ws";

const TRUSTED_CLIENT_TOKEN = "6A5AA1D4EAFF4E9FB37E23D68491D6F4";
const BASE_URL = "speech.platform.bing.com/consumer/speech/synthesize/readaloud";
const WSS_URL = `wss://${BASE_URL}/edge/v1?TrustedClientToken=${TRUSTED_CLIENT_TOKEN}`;
const VOICE_LIST_URL = `https://${BASE_URL}/voices/list?trustedclienttoken=${TRUSTED_CLIENT_TOKEN}`;

const CHROMIUM_FULL_VERSION = "143.0.3650.75";
const CHROMIUM_MAJOR_VERSION = CHROMIUM_FULL_VERSION.split(".")[0];
const SEC_MS_GEC_VERSION = `1-${CHROMIUM_FULL_VERSION}`;

/** Seconds between the Windows file-time epoch (1601) and the Unix epoch. */
const WIN_EPOCH = 11644473600;

const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " +
  `Chrome/${CHROMIUM_MAJOR_VERSION}.0.0.0 Safari/537.36 Edg/${CHROMIUM_MAJOR_VERSION}.0.0.0`;

const WSS_HEADERS = {
  "User-Agent": USER_AGENT,
  "Accept-Language": "en-US,en;q=0.9",
  Pragma: "no-cache",
  "Cache-Control": "no-cache",
  Origin: "chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold",
};

export interface Boundary {
  text: string;
  startMs: number;
  durationMs: number;
  kind: "word" | "sentence";
}

export interface SynthesisResult {
  audio: Buffer;
  boundaries: Boundary[];
}

export interface SynthesisOptions {
  voice?: string;
  rate?: string;
  pitch?: string;
  volume?: string;
  boundary?: "word" | "sentence";
  timeoutMs?: number;
  /** Only used when running behind an HTTP proxy; unset in production. */
  agent?: unknown;
  /** Fragments synthesized in parallel when the text needs splitting. */
  concurrency?: number;
}

export interface EdgeVoice {
  name: string;
  locale: string;
  gender: string;
  description: string;
}

/**
 * The `Sec-MS-GEC` token: SHA-256 of the current Windows file time (in
 * 100-nanosecond ticks, floored to a 5-minute boundary) concatenated with the
 * trusted client token, upper-cased hex.
 */
export function generateSecMsGec(now: number = Date.now()): string {
  let ticks = Math.floor(now / 1000) + WIN_EPOCH;
  ticks -= ticks % 300;
  // Seconds -> 100-nanosecond intervals. Exceeds 2^53, so use BigInt.
  const fileTime = BigInt(ticks) * 10_000_000n;
  return createHash("sha256")
    .update(`${fileTime}${TRUSTED_CLIENT_TOKEN}`, "ascii")
    .digest("hex")
    .toUpperCase();
}

function connectId(): string {
  return randomUUID().replace(/-/g, "");
}

/** Microsoft expects a JavaScript `Date.toString()`-shaped timestamp. */
function dateToString(): string {
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${days[d.getUTCDay()]} ${months[d.getUTCMonth()]} ${pad(d.getUTCDate())} ` +
    `${d.getUTCFullYear()} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:` +
    `${pad(d.getUTCSeconds())} GMT+0000 (Coordinated Universal Time)`
  );
}

function escapeXml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/**
 * Strip characters the service rejects: control codes and the XML-illegal
 * range, which otherwise close the connection with no error message.
 */
function removeIncompatibleCharacters(text: string): string {
  return [...text]
    .map((ch) => {
      const code = ch.codePointAt(0)!;
      const illegal =
        (code >= 0 && code <= 8) ||
        (code >= 11 && code <= 12) ||
        (code >= 14 && code <= 31);
      return illegal ? " " : ch;
    })
    .join("");
}

/** Split text so each SSML payload stays under the service's frame limit. */
export function splitText(text: string, maxChars = 2000): string[] {
  const clean = removeIncompatibleCharacters(text).trim();
  if (clean.length <= maxChars) return clean ? [clean] : [];

  const parts: string[] = [];
  let remaining = clean;
  while (remaining.length > maxChars) {
    const window = remaining.slice(0, maxChars);
    let cut = Math.max(
      window.lastIndexOf(". "),
      window.lastIndexOf("! "),
      window.lastIndexOf("? "),
      window.lastIndexOf("\n"),
    );
    if (cut < maxChars / 3) cut = window.lastIndexOf(" ");
    if (cut <= 0) cut = maxChars;
    else cut += 1;
    parts.push(remaining.slice(0, cut).trim());
    remaining = remaining.slice(cut).trim();
  }
  if (remaining) parts.push(remaining);
  return parts;
}

function parseBinaryFrame(data: Buffer): Buffer | null {
  if (data.length < 2) return null;
  const headerLength = data.readUInt16BE(0);
  if (headerLength + 2 > data.length) return null;
  const headers = data.subarray(2, headerLength + 2).toString("ascii");
  if (!headers.includes("Path:audio")) return null;
  return data.subarray(headerLength + 2);
}

function parseMetadata(text: string, kind: "word" | "sentence"): Boundary[] {
  const separator = text.indexOf("\r\n\r\n");
  if (separator === -1) return [];
  let payload: { Metadata?: Array<Record<string, any>> };
  try {
    payload = JSON.parse(text.slice(separator + 4));
  } catch {
    return [];
  }
  const out: Boundary[] = [];
  for (const item of payload.Metadata ?? []) {
    if (item.Type !== "WordBoundary" && item.Type !== "SentenceBoundary") continue;
    out.push({
      text: item.Data?.text?.Text ?? "",
      // Microsoft reports offsets in 100-nanosecond ticks.
      startMs: (item.Data?.Offset ?? 0) / 10_000,
      durationMs: (item.Data?.Duration ?? 0) / 10_000,
      kind,
    });
  }
  return out;
}

/** Synthesize one text fragment. Fragments over ~2000 chars must be split first. */
export function synthesizeFragment(
  text: string,
  options: SynthesisOptions = {},
): Promise<SynthesisResult> {
  const {
    voice = "en-US-EmmaMultilingualNeural",
    rate = "+0%",
    pitch = "+0Hz",
    volume = "+0%",
    boundary = "sentence",
    timeoutMs = 20_000,
    agent,
  } = options;

  const wantsWords = boundary === "word";
  const requestId = connectId();
  const url =
    `${WSS_URL}&ConnectionId=${requestId}` +
    `&Sec-MS-GEC=${generateSecMsGec()}` +
    `&Sec-MS-GEC-Version=${SEC_MS_GEC_VERSION}`;

  return new Promise<SynthesisResult>((resolve, reject) => {
    const socket = new WebSocket(url, {
      headers: WSS_HEADERS,
      ...(agent ? { agent } : {}),
    } as any);

    const chunks: Buffer[] = [];
    const boundaries: Boundary[] = [];
    let settled = false;

    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try {
        socket.close();
      } catch {
        /* the socket may already be closing */
      }
      if (error) reject(error);
      else if (chunks.length === 0) reject(new Error("no audio received from Edge"));
      else resolve({ audio: Buffer.concat(chunks), boundaries });
    };

    const timer = setTimeout(
      () => finish(new Error(`Edge synthesis timed out after ${timeoutMs}ms`)),
      timeoutMs,
    );

    socket.on("open", () => {
      socket.send(
        `X-Timestamp:${dateToString()}\r\n` +
          "Content-Type:application/json; charset=utf-8\r\n" +
          "Path:speech.config\r\n\r\n" +
          '{"context":{"synthesis":{"audio":{"metadataoptions":{' +
          `"sentenceBoundaryEnabled":"${!wantsWords}","wordBoundaryEnabled":"${wantsWords}"` +
          '},"outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}\r\n',
      );

      const ssml =
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'>" +
        `<voice name='${voice}'>` +
        `<prosody pitch='${pitch}' rate='${rate}' volume='${volume}'>` +
        `${escapeXml(removeIncompatibleCharacters(text))}` +
        "</prosody></voice></speak>";

      socket.send(
        `X-RequestId:${requestId}\r\n` +
          "Content-Type:application/ssml+xml\r\n" +
          // The trailing Z on an already-formatted date is a Microsoft quirk;
          // the service rejects the frame without it.
          `X-Timestamp:${dateToString()}Z\r\n` +
          "Path:ssml\r\n\r\n" +
          ssml,
      );
    });

    socket.on("message", (data: Buffer, isBinary: boolean) => {
      if (isBinary) {
        const audio = parseBinaryFrame(data);
        if (audio?.length) chunks.push(audio);
        return;
      }
      const text = data.toString("utf-8");
      if (text.includes("Path:audio.metadata")) {
        boundaries.push(...parseMetadata(text, wantsWords ? "word" : "sentence"));
      } else if (text.includes("Path:turn.end")) {
        finish();
      }
    });

    socket.on("error", (err: Error) => finish(err));
    socket.on("close", (code: number) => {
      if (code === 1000 || chunks.length > 0) finish();
      else finish(new Error(`Edge closed the connection (code ${code})`));
    });
  });
}

/** Run `worker` over `items` with at most `limit` in flight, preserving order. */
async function mapWithConcurrency<T, R>(
  items: T[],
  limit: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let next = 0;

  const runners = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (true) {
      const index = next++;
      if (index >= items.length) return;
      results[index] = await worker(items[index], index);
    }
  });

  await Promise.all(runners);
  return results;
}

/**
 * Synthesize text of any length by splitting it and concatenating the results.
 * MP3 frames are self-delimiting and every fragment uses the same encoder
 * settings, so a plain buffer join produces a valid stream.
 *
 * Fragments are synthesized in parallel — the round trip to Microsoft dominates
 * the wall clock, so this is what keeps a multi-fragment request inside the
 * function timeout. Offsets are applied afterwards, in order.
 */
export async function synthesize(
  text: string,
  options: SynthesisOptions = {},
): Promise<SynthesisResult> {
  const fragments = splitText(text);
  if (fragments.length === 0) throw new Error("nothing to synthesize");

  const concurrency = options.concurrency ?? 4;
  const parts = await mapWithConcurrency(fragments, concurrency, (fragment) =>
    synthesizeFragment(fragment, options),
  );

  const audio: Buffer[] = [];
  const boundaries: Boundary[] = [];
  let offsetMs = 0;

  for (const part of parts) {
    audio.push(part.audio);
    for (const mark of part.boundaries) {
      boundaries.push({ ...mark, startMs: mark.startMs + offsetMs });
    }
    // 48 kbps constant bitrate: 6000 bytes per second of audio, exactly.
    offsetMs += (part.audio.length / 6000) * 1000;
  }

  return { audio: Buffer.concat(audio), boundaries };
}

export async function listVoices(): Promise<EdgeVoice[]> {
  const response = await fetch(VOICE_LIST_URL, {
    headers: { "User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9" },
  });
  if (!response.ok) throw new Error(`voice list failed: HTTP ${response.status}`);

  const raw = (await response.json()) as Array<Record<string, any>>;
  return raw
    .map((item) => ({
      name: item.ShortName as string,
      locale: (item.Locale ?? "") as string,
      gender: (item.Gender ?? "") as string,
      description: (item.VoiceTag?.VoicePersonalities ?? []).join(", "),
    }))
    .sort((a, b) => a.locale.localeCompare(b.locale) || a.name.localeCompare(b.name));
}
