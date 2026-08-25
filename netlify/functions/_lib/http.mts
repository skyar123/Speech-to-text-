/** Shared request handling: auth, OpenAI-shaped errors, input limits. */

/** Hard ceiling on a single request, matching OpenAI's own 4096-char limit
 *  plus headroom. Also the main guard against someone running up your
 *  function minutes with a novel. */
export const MAX_INPUT_CHARS = Number(Netlify.env.get("TTSKIT_MAX_INPUT_CHARS") ?? 5000);

export const MODEL_IDS = ["tts-1", "tts-1-hd", "gpt-4o-mini-tts", "ttskit"];

export function errorResponse(
  status: number,
  message: string,
  { param, code, type }: { param?: string; code?: string; type?: string } = {},
): Response {
  return Response.json(
    {
      error: {
        message,
        type: type ?? (status < 500 ? "invalid_request_error" : "api_error"),
        param: param ?? null,
        code: code ?? null,
      },
    },
    { status },
  );
}

/**
 * Enforce `TTSKIT_API_KEY` when it is set. When it is unset the endpoint is
 * open — convenient for a personal demo, but see the deploy notes: an open
 * endpoint is a standing invitation to burn your function minutes.
 */
export function checkAuth(req: Request): Response | null {
  const required = Netlify.env.get("TTSKIT_API_KEY");
  if (!required) return null;
  const presented = (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "").trim();
  if (presented !== required) {
    return errorResponse(401, "Invalid API key", { code: "invalid_api_key" });
  }
  return null;
}

export function checkLength(text: string): Response | null {
  if (text.length <= MAX_INPUT_CHARS) return null;
  return errorResponse(
    413,
    `Input is ${text.length} characters; this hosted endpoint accepts ${MAX_INPUT_CHARS}. ` +
      "Run ttskit locally (`ttskit book`) for long-form documents — it has no such limit.",
    { param: "input", code: "input_too_long" },
  );
}
