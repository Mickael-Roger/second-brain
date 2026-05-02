// Push-to-talk button that pipes browser-native speech recognition into the
// parent's text field via `onTranscript`. Renders nothing when the browser
// doesn't expose the Web Speech API.
//
// Behaviour:
//   - Click → request mic permission, listen for one utterance.
//   - The recogniser auto-stops after the user's pause and fires the final
//     transcript once. To dictate more, the user clicks again.
//   - Click while listening → stop early (whatever was finalised so far is
//     still appended).
//   - The component does NOT auto-send; the caller's onTranscript handler
//     appends to its own text state and the user reviews / hits send.
//
// Why single-utterance instead of continuous: with `continuous: true` +
// `interimResults: false`, several browsers (Chrome notably) fail to mark
// any result as final and the input stays empty. The single-utterance
// pattern is the canonical Web Speech use and works across Chrome, Edge,
// Safari, and recent Firefox.

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
    // Defensive: if a stale recogniser is still around (Firefox can leave
    // a session dangling when neither onend nor onerror fires), abort it
    // before opening a new one — otherwise the browser may refuse the
    // second start() with InvalidStateError.
    if (recRef.current) {
      try {
        recRef.current.abort();
      } catch {
        /* already dead */
      }
      recRef.current = null;
    }
    const rec = new Ctor();
    // currentLanguage returns "fr" / "en"; SpeechRecognition wants a
    // BCP47 tag with a region. The default region picks something
    // reasonable rather than letting the browser fall back to en-US.
    const lang = currentLanguage() === "fr" ? "fr-FR" : "en-US";
    rec.lang = lang;
    rec.continuous = false;
    // interimResults=true is needed for Firefox: its implementation
    // sometimes ends a session without ever flipping a result to
    // isFinal, so we keep the latest interim around as a fallback to
    // commit in onend. Chrome behaves the same either way — we still
    // only forward to onTranscript when a result is marked final.
    rec.interimResults = true;

    let pendingInterim = "";

    rec.onresult = (e) => {
      let finalText = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) {
          finalText += r[0].transcript;
        } else {
          // Most-recent interim wins — the spec says each result
          // refines in place across events.
          pendingInterim = r[0].transcript;
        }
      }
      if (finalText.trim()) {
        onTranscript(finalText);
        pendingInterim = "";
      }
    };
    rec.onerror = (e) => {
      // Surface error codes (no-speech, audio-capture, not-allowed,
      // network, …) so a missing-mic / denied-permission case isn't
      // silent in DevTools.
      // eslint-disable-next-line no-console
      console.warn("[MicButton] speech recognition error:", e?.error);
      setListening(false);
    };
    rec.onend = () => {
      // Firefox safety net — commit whatever interim we held when no
      // final result was ever dispatched. Chrome reaches this branch
      // with pendingInterim already empty.
      if (pendingInterim.trim()) {
        onTranscript(pendingInterim);
        pendingInterim = "";
      }
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
    // Reset state optimistically: Firefox sometimes doesn't fire onend
    // after stop(), which would leave the button stuck in its listening
    // pulse forever. We use stop() (not abort()) so any in-flight final
    // result still has a chance to land via the existing onresult
    // handler before the session tears down.
    const rec = recRef.current;
    recRef.current = null;
    setListening(false);
    try {
      rec?.stop();
    } catch {
      /* already dead */
    }
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
