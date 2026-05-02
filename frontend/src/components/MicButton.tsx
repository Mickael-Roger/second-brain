// Push-to-talk button that streams browser-native speech recognition into
// the parent's text field via `onTranscript`. Renders nothing when the
// browser doesn't expose the Web Speech API (Firefox today, plus older
// embedded webviews) so the surrounding composer stays clean.
//
// Behaviour:
//   - First click  → request mic permission and start listening (continuous).
//   - Second click → stop. The component does NOT auto-send; the caller's
//                    onTranscript handler appends finalised speech to its
//                    own text state, leaving the user free to edit / hit send.
//   - Final results only — interim transcripts would flicker the textarea
//                          with half-formed words. The mic icon's pulse
//                          indicates we're listening.

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Mic } from "lucide-react";

import { currentLanguage } from "@/lib/i18n";

// Minimal structural subset of the SpeechRecognition we touch — TS's
// lib.dom.d.ts has the full types but the prefixed webkit constructor
// isn't on Window by default, so we cast through this.
interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: SpeechRecognitionEventLike) => void) | null;
  onerror: ((e: { error?: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<{
    isFinal: boolean;
    0: { transcript: string };
  }>;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function getRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

interface Props {
  onTranscript: (text: string) => void;
  disabled?: boolean;
  className?: string;
  // Visual size. Composer uses 5 (matches the send/image buttons),
  // tighter compact dialogs use 4. Defaults to 5.
  iconSize?: 4 | 5;
}

export default function MicButton({
  onTranscript,
  disabled,
  className,
  iconSize = 5,
}: Props) {
  const { t } = useTranslation();
  const [listening, setListening] = useState(false);
  const recRef = useRef<SpeechRecognitionLike | null>(null);
  const Ctor = getRecognitionCtor();

  // Make sure we let go of the mic if the surrounding component unmounts
  // mid-recording (modal close, view switch).
  useEffect(
    () => () => {
      recRef.current?.abort();
      recRef.current = null;
    },
    [],
  );

  if (!Ctor) return null;

  function start() {
    if (listening || !Ctor) return;
    const rec = new Ctor();
    // currentLanguage returns "fr" / "en"; SpeechRecognition wants a
    // BCP47 tag with a region. The default region picks something
    // reasonable rather than letting the browser fall back to en-US.
    const lang = currentLanguage() === "fr" ? "fr-FR" : "en-US";
    rec.lang = lang;
    rec.continuous = true;
    rec.interimResults = false;
    rec.onresult = (e) => {
      let chunk = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) chunk += r[0].transcript;
      }
      if (chunk) onTranscript(chunk);
    };
    rec.onerror = () => {
      setListening(false);
    };
    rec.onend = () => {
      setListening(false);
      recRef.current = null;
    };
    try {
      rec.start();
      recRef.current = rec;
      setListening(true);
    } catch {
      // start() throws InvalidStateError if a session is already
      // running — defensive no-op.
    }
  }

  function stop() {
    recRef.current?.stop();
  }

  const iconClass = iconSize === 4 ? "h-4 w-4" : "h-5 w-5";

  return (
    <button
      type="button"
      onClick={listening ? stop : start}
      disabled={disabled}
      aria-label={listening ? t("speech.stop") : t("speech.start")}
      title={listening ? t("speech.stop") : t("speech.start")}
      className={
        className ??
        `flex items-center justify-center rounded-lg p-1.5 transition ${
          listening
            ? "bg-red-500/15 text-red-400 hover:bg-red-500/25"
            : "text-muted hover:bg-bg hover:text-text"
        } disabled:cursor-not-allowed disabled:opacity-50`
      }
    >
      <Mic className={`${iconClass} ${listening ? "animate-pulse" : ""}`} />
    </button>
  );
}
