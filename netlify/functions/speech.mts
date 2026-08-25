import type { Config, Context } from "@netlify/functions";

import { synthesize } from "./_lib/edge-tts.mts";
import { checkAuth, checkLength, errorResponse } from "./_lib/http.mts";
import { resolveVoice, speedToRate } from "./_lib/voices.mts";

/**
 * OpenAI-compatible `POST /v1/audio/speech`.
 *
 * MP3 only: transcoding needs ffmpeg, which is not available in a Netlify
 * function. The local `ttskit serve` covers opus/aac/flac/wav/pcm.
 */
export default async (req: Request, context: Context) => {
  if (req.method !== "POST") {
    return errorResponse(405, "Use POST for /v1/audio/speech");
  }

  const unauthorized = checkAuth(req);
  if (unauthorized) return unauthorized;

  let body: Record<string, any>;
  try {
    body = await req.json();
  } catch {
    return errorResponse(400, "Request body must be valid JSON");
  }

  const input = typeof body.input === "string" ? body.input.trim() : "";
  if (!input) {
    return errorResponse(400, "'input' must be a non-empty string", { param: "input" });
  }

  const tooLong = checkLength(input);
  if (tooLong) return tooLong;

  const format = String(body.response_format ?? "mp3").toLowerCase();
  if (format !== "mp3") {
    return errorResponse(
      400,
      `This hosted endpoint only returns mp3; '${format}' needs ffmpeg, which serverless ` +
        "functions do not provide. Run `ttskit serve` locally for opus, aac, flac, wav and pcm.",
      { param: "response_format", code: "format_unavailable" },
    );
  }

  const speed = Number(body.speed ?? 1);
  if (!Number.isFinite(speed) || speed < 0.25 || speed > 4) {
    return errorResponse(400, "'speed' must be between 0.25 and 4", { param: "speed" });
  }

  const voice = resolveVoice(body.voice);
  const wantsWordMarks = body.boundary === "word";

  try {
    const started = Date.now();
    const result = await synthesize(input, {
      voice,
      rate: typeof body.rate === "string" ? body.rate : speedToRate(speed),
      pitch: typeof body.pitch === "string" ? body.pitch : "+0Hz",
      volume: typeof body.volume === "string" ? body.volume : "+0%",
      boundary: wantsWordMarks ? "word" : "sentence",
    });

    return new Response(new Uint8Array(result.audio), {
      headers: {
        "Content-Type": "audio/mpeg",
        "Content-Length": String(result.audio.length),
        "Cache-Control": "no-store",
        "X-TTSKit-Voice": voice,
        "X-TTSKit-Engine": "edge",
        "X-TTSKit-Ms": String(Date.now() - started),
      },
    });
  } catch (error) {
    console.error("synthesis failed", { requestId: context.requestId, error });
    return errorResponse(502, `Synthesis failed: ${(error as Error).message}`, {
      type: "api_error",
    });
  }
};

export const config: Config = {
  path: ["/v1/audio/speech", "/audio/speech"],
};
