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

type ToastKind = "success" | "error";

type ToastState = { message: string; kind: ToastKind; id: number };

type ToastContextValue = {
  showToast: (message: string, kind?: ToastKind) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

export function WorkspaceToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<ToastState | null>(null);
  const enter = useMotionSafe(motionStd);
  const exit = useMotionSafe(motionExit);

  const showToast = useCallback(
    (message: string, kind: ToastKind = "success") => {
      setToast({ message, kind, id: Date.now() });
    },
    [],
  );

  useEffect(() => {
    if (!toast) return;
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
              className="relative overflow-hidden rounded-lg border border-[var(--border)] bg-[color-mix(in_srgb,var(--panel)_90%,transparent)] px-4 py-2 font-mono text-xs text-foreground backdrop-blur"
              style={{
                boxShadow: `inset 2px 0 0 ${
                  toast.kind === "error"
                    ? "var(--c-red)"
                    : "var(--c-accent)"
                }, var(--panel-shadow)`,
              }}
            >
              {toast.message}
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
