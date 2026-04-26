import { KeyboardEvent, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Image as ImageIcon, Send, Square, X } from "lucide-react";

import type { ContentBlock } from "@/lib/api";

interface Props {
  onSend: (blocks: ContentBlock[]) => void;
  onStop?: () => void;
  busy: boolean;
}

interface PendingImage {
  mime: string;
  data: string;
  preview: string;
}

async function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const r = reader.result as string;
      const idx = r.indexOf(",");
      resolve(r.slice(idx + 1));
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export default function Composer({ onSend, onStop, busy }: Props) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const [images, setImages] = useState<PendingImage[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const textArea = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = textArea.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [text]);

  function handleKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  async function onPickImages(files: FileList | null) {
    if (!files) return;
    const next: PendingImage[] = [];
    for (const f of Array.from(files)) {
      if (!f.type.startsWith("image/")) continue;
      const data = await fileToBase64(f);
      next.push({
        mime: f.type,
        data,
        preview: `data:${f.type};base64,${data}`,
      });
    }
    setImages([...images, ...next]);
  }

  function submit() {
    if (busy) return;
    const blocks: ContentBlock[] = [];
    for (const img of images) blocks.push({ type: "image", mime: img.mime, data: img.data });
    if (text.trim()) blocks.push({ type: "text", text: text.trim() });
    if (!blocks.length) return;
    onSend(blocks);
    setText("");
    setImages([]);
  }

  return (
    <div className="border-t border-border bg-surface px-4 py-3">
      <div className="mx-auto max-w-3xl">
        {images.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {images.map((img, i) => (
              <div key={i} className="relative">
                <img
                  src={img.preview}
                  alt=""
                  className="h-16 w-16 rounded border border-border object-cover"
                />
                <button
                  onClick={() => setImages(images.filter((_, j) => j !== i))}
                  className="absolute -right-1 -top-1 rounded-full bg-bg p-0.5 text-muted hover:text-red-400"
                  aria-label="Remove"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="flex items-end gap-2 rounded-2xl border border-border bg-bg px-3 py-2">
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            className="text-muted hover:text-text"
            disabled={busy}
            aria-label="Attach image"
          >
            <ImageIcon className="h-5 w-5" />
          </button>
          <input
            ref={fileInput}
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            onChange={(e) => onPickImages(e.target.files)}
          />
          <textarea
            ref={textArea}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKey}
            rows={1}
            placeholder={t("chat.placeholder")}
            disabled={busy}
            className="flex-1 max-h-[10rem] min-h-[1.5rem] resize-none overflow-y-auto py-1 outline-none"
          />
          {busy ? (
            <button
              type="button"
              onClick={onStop}
              className="rounded-lg bg-red-500/20 p-2 text-red-300 hover:bg-red-500/30"
              aria-label={t("chat.stop")}
            >
              <Square className="h-4 w-4" />
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={!text.trim() && images.length === 0}
              className="rounded-lg bg-accent p-2 text-bg disabled:bg-border"
              aria-label={t("chat.send")}
            >
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
