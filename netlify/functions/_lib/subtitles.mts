/**
 * Timing marks -> SRT / VTT.
 *
 * Mirrors `ttskit/subtitles.py`; cues break on a pause, a sentence end, a
 * character budget, or a duration ceiling.
 */

import type { Boundary } from "./edge-tts.mts";

const MAX_CHARS = 42;
const MAX_MS = 6000;
const MAX_GAP_MS = 700;

export interface Cue {
  index: number;
  startMs: number;
  endMs: number;
  text: string;
}

function timestamp(ms: number, comma: boolean): string {
  const total = Math.max(0, Math.round(ms));
  const hours = Math.floor(total / 3_600_000);
  const minutes = Math.floor(total / 60_000) % 60;
  const seconds = Math.floor(total / 1000) % 60;
  const millis = total % 1000;
  const pad = (n: number, width = 2) => String(n).padStart(width, "0");
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}${comma ? "," : "."}${pad(millis, 3)}`;
}

function joinWords(parts: string[]): string {
  let out = "";
  for (const part of parts) {
    const token = part.trim();
    if (!token) continue;
    if (!out) out = token;
    else if (/^[,.!?;:%)\]}'"…]/.test(token)) out += token;
    else if (/[([{“‘$]$/.test(out)) out += token;
    else out += ` ${token}`;
  }
  return out;
}

export function groupBoundaries(boundaries: Boundary[]): Cue[] {
  const marks = boundaries.filter((b) => b.text?.trim());
  if (marks.length === 0) return [];

  if (marks.every((b) => b.kind === "sentence")) {
    const cues: Cue[] = [];
    let previousEnd = 0;
    for (const mark of marks) {
      const start = Math.max(mark.startMs, previousEnd);
      const end = Math.max(start, mark.startMs + mark.durationMs);
      cues.push({ index: cues.length + 1, startMs: start, endMs: end, text: mark.text.trim() });
      previousEnd = end;
    }
    return cues;
  }

  const cues: Cue[] = [];
  let current: Boundary[] = [];

  const flush = () => {
    if (current.length === 0) return;
    cues.push({
      index: cues.length + 1,
      startMs: current[0].startMs,
      endMs: current[current.length - 1].startMs + current[current.length - 1].durationMs,
      text: joinWords(current.map((w) => w.text)),
    });
    current = [];
  };

  for (const mark of marks) {
    if (current.length > 0) {
      const last = current[current.length - 1];
      const gap = mark.startMs - (last.startMs + last.durationMs);
      const projected = joinWords([...current.map((w) => w.text), mark.text]);
      const span = mark.startMs + mark.durationMs - current[0].startMs;
      if (gap > MAX_GAP_MS || projected.length > MAX_CHARS || span > MAX_MS) flush();
      else if (/[.!?…]$/.test(last.text.trim())) flush();
    }
    current.push(mark);
  }
  flush();
  return cues;
}

export function toSrt(boundaries: Boundary[]): string {
  const cues = groupBoundaries(boundaries);
  if (cues.length === 0) return "";
  return (
    cues
      .map(
        (cue) =>
          `${cue.index}\n${timestamp(cue.startMs, true)} --> ${timestamp(cue.endMs, true)}\n${cue.text}`,
      )
      .join("\n\n") + "\n"
  );
}

export function toVtt(boundaries: Boundary[]): string {
  const cues = groupBoundaries(boundaries);
  const lines = ["WEBVTT", ""];
  for (const cue of cues) {
    lines.push(String(cue.index));
    lines.push(`${timestamp(cue.startMs, false)} --> ${timestamp(cue.endMs, false)}`);
    lines.push(cue.text);
    lines.push("");
  }
  return lines.join("\n");
}
