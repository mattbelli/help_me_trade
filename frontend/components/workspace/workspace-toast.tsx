"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { AnimatePresence, motion } from "motion/react";

import { motionExit, motionStd, useMotionSafe } from "@/lib/workspace/motion";

type ToastKind = "success" | "error" | "info";

type ToastAction = { label: string; onClick: () => void };

type ToastState = {
  message: string;
  kind: ToastKind;
  id: number;
  action?: ToastAction;
};

type ToastContextValue = {
  showToast: (message: string, kind?: ToastKind, action?: ToastAction) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

export function WorkspaceToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<ToastState | null>(null);
  const enter = useMotionSafe(motionStd);
  const exit = useMotionSafe(motionExit);

  const showToast = useCallback(
    (message: string, kind: ToastKind = "success", action?: ToastAction) => {
      setToast({ message, kind, id: Date.now(), action });
    },
    [],
  );

  useEffect(() => {
    // "info" toasts are persistent (used for the reload-to-update prompt) —
    // they stay until acted on. success/error keep the 2.8s auto-dismiss.
    if (!toast || toast.kind === "info") return;
    const id = setTimeout(() => setToast(null), 2800);
    return () => clearTimeout(id);
  }, [toast]);

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      <div className="pointer-events-none fixed bottom-20 left-1/2 z-[80] -translate-x-1/2">
        <AnimatePresence>
          {toast ? (
            <motion.div
              key={toast.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 6, transition: exit }}
              transition={enter}
              className="relative overflow-hidden rounded-lg border border-[var(--border)] bg-[color-mix(in_srgb,var(--panel)_90%,transparent)] px-4 py-2 font-mono text-[length:var(--fs-body)] text-foreground backdrop-blur"
              style={{
                boxShadow: `inset 2px 0 0 ${
                  toast.kind === "error"
                    ? "var(--c-red)"
                    : "var(--c-accent)"
                }, var(--panel-shadow)`,
              }}
            >
              {toast.message}
              {toast.action ? (
                <button
                  type="button"
                  onClick={() => {
                    toast.action?.onClick();
                    setToast(null);
                  }}
                  className="pointer-events-auto ml-3 rounded border border-[var(--border)] px-2 py-0.5 font-mono text-[length:var(--fs-table)] font-semibold text-[var(--c-accent)] transition-colors hover:bg-[var(--c-accent-soft)]"
                >
                  {toast.action.label}
                </button>
              ) : (
                <span
                  aria-hidden
                  className="toast-countdown"
                  style={{
                    background:
                      toast.kind === "error"
                        ? "var(--c-red)"
                        : "var(--c-accent)",
                  }}
                />
              )}
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

export function useWorkspaceToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useWorkspaceToast must be used within WorkspaceToastProvider");
  }
  return ctx;
}
