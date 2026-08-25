/**
 * Voice-name translation for the hosted API.
 *
 * Mirrors `ttskit/voices.py` — keep the two tables in sync so a voice name
 * behaves identically whether it is served locally or from Netlify.
 */

export const OPENAI_TO_EDGE: Record<string, string> = {
  alloy: "en-US-JennyNeural",
  echo: "en-US-GuyNeural",
  fable: "en-GB-SoniaNeural",
  onyx: "en-US-ChristopherNeural",
  nova: "en-US-AriaNeural",
  shimmer: "en-US-MichelleNeural",
  ash: "en-US-AndrewNeural",
  ballad: "en-GB-RyanNeural",
  coral: "en-US-EmmaNeural",
  sage: "en-US-AvaNeural",
  verse: "en-US-BrianNeural",
};

export const EDGE_ALIASES: Record<string, string> = {
  jenny: "en-US-JennyNeural",
  guy: "en-US-GuyNeural",
  aria: "en-US-AriaNeural",
  ava: "en-US-AvaNeural",
  andrew: "en-US-AndrewNeural",
  emma: "en-US-EmmaNeural",
  brian: "en-US-BrianNeural",
  christopher: "en-US-ChristopherNeural",
  michelle: "en-US-MichelleNeural",
  eric: "en-US-EricNeural",
  roger: "en-US-RogerNeural",
  steffan: "en-US-SteffanNeural",
  sonia: "en-GB-SoniaNeural",
  ryan: "en-GB-RyanNeural",
  libby: "en-GB-LibbyNeural",
  natasha: "en-AU-NatashaNeural",
  william: "en-AU-WilliamNeural",
  clara: "en-CA-ClaraNeural",
  neerja: "en-IN-NeerjaNeural",
  prabhat: "en-IN-PrabhatNeural",
};

export const OPENAI_VOICES = Object.keys(OPENAI_TO_EDGE);

/** Resolve an OpenAI name, a short alias, or a native Edge name. */
export function resolveVoice(voice?: string | null, fallback = "alloy"): string {
  const name = (voice ?? "").trim() || fallback;
  const key = name.toLowerCase();
  return OPENAI_TO_EDGE[key] ?? EDGE_ALIASES[key] ?? name;
}

/** OpenAI's `speed` multiplier as the percentage string Edge expects. */
export function speedToRate(speed: number | undefined): string {
  const value = Number.isFinite(speed) ? Number(speed) : 1;
  const percent = Math.round((value - 1) * 100);
  return `${percent >= 0 ? "+" : ""}${percent}%`;
}
