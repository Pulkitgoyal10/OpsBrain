"use client";

import { useCallback, useRef, useState } from "react";

export type ToastVariant = "info" | "success" | "error";

interface ToastItem {
  id: string;
  message: string;
  variant: ToastVariant;
}

const AUTO_DISMISS_MS = 4000;

// Self-contained toast queue - no external dependency needed for a handful
// of transient upload-status notifications.
export function useToasts() {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback(
    (message: string, variant: ToastVariant = "info") => {
      const id = `${Date.now()}-${Math.random()}`;
      setToasts((prev) => [...prev, { id, message, variant }]);
      timers.current.set(
        id,
        setTimeout(() => dismiss(id), AUTO_DISMISS_MS)
      );
    },
    [dismiss]
  );

  return { toasts, push, dismiss };
}

const VARIANT_STYLES: Record<ToastVariant, string> = {
  info: "border-crimson/40 text-stark",
  success: "border-crimson text-stark",
  error: "border-status text-status",
};

export function ToastStack({
  toasts,
  onDismiss,
}: {
  toasts: ToastItem[];
  onDismiss: (id: string) => void;
}) {
  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 max-w-[90vw] sm:max-w-sm">
      {toasts.map((t) => (
        <div
          key={t.id}
          onClick={() => onDismiss(t.id)}
          role="status"
          className={`bg-carbon border ${VARIANT_STYLES[t.variant]} rounded-lg px-4 py-3 text-sm shadow-lg cursor-pointer break-words`}
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}
