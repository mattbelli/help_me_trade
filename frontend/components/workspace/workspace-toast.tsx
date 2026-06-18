"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

type ToastContextValue = {
  showToast: (message: string) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

export function WorkspaceToastProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState<string | null>(null);

  const showToast = useCallback((next: string) => {
    setMessage(next);
  }, []);

  useEffect(() => {
    if (!message) return;
    const id = setTimeout(() => setMessage(null), 2800);
    return () => clearTimeout(id);
  }, [message]);

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      {message ? (
        <div className="pointer-events-none fixed bottom-20 left-1/2 z-[80] -translate-x-1/2">
          <div className="rounded-lg border border-border bg-popover px-4 py-2 font-mono text-xs text-popover-foreground shadow-lg">
            {message}
          </div>
        </div>
      ) : null}
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
