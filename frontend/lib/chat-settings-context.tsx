"use client";

import {
  createContext,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  DEFAULT_CHAT_SETTINGS,
  type ChatSettings,
} from "@/lib/chat-toolbar-config";

type ChatSettingsContextValue = {
  settings: ChatSettings;
  setSettings: React.Dispatch<React.SetStateAction<ChatSettings>>;
  settingsRef: React.RefObject<ChatSettings>;
};

const ChatSettingsContext = createContext<ChatSettingsContextValue | null>(
  null,
);

export function ChatSettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<ChatSettings>(DEFAULT_CHAT_SETTINGS);
  const settingsRef = useRef(settings);
  settingsRef.current = settings;

  const value = useMemo(
    () => ({ settings, setSettings, settingsRef }),
    [settings],
  );

  return (
    <ChatSettingsContext.Provider value={value}>
      {children}
    </ChatSettingsContext.Provider>
  );
}

export function useChatSettings() {
  const ctx = useContext(ChatSettingsContext);
  if (!ctx) {
    throw new Error("useChatSettings must be used within ChatSettingsProvider");
  }
  return ctx;
}
