"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import type { MemoryRecord } from "@/lib/memories";

type LearnedMap = Record<string, MemoryRecord[]>;

type LearnedMemoriesContextValue = {
  byMessage: LearnedMap;
  setLearnedForMessage: (messageId: string, memories: MemoryRecord[]) => void;
  clearLearnedMemories: () => void;
  removeLearnedMemory: (messageId: string, memoryId: number) => void;
  updateLearnedMemory: (messageId: string, memory: MemoryRecord) => void;
};

const LearnedMemoriesContext =
  createContext<LearnedMemoriesContextValue | null>(null);

export function LearnedMemoriesProvider({ children }: { children: ReactNode }) {
  const [byMessage, setByMessage] = useState<LearnedMap>({});

  const setLearnedForMessage = useCallback(
    (messageId: string, memories: MemoryRecord[]) =>
      setByMessage((prev) => ({ ...prev, [messageId]: memories })),
    [],
  );
  const clearLearnedMemories = useCallback(() => setByMessage({}), []);
  const removeLearnedMemory = useCallback(
    (messageId: string, memoryId: number) =>
      setByMessage((prev) => {
        const items = prev[messageId];
        if (!items) return prev;
        return { ...prev, [messageId]: items.filter((m) => m.id !== memoryId) };
      }),
    [],
  );
  const updateLearnedMemory = useCallback(
    (messageId: string, memory: MemoryRecord) =>
      setByMessage((prev) => {
        const items = prev[messageId];
        if (!items) return prev;
        return {
          ...prev,
          [messageId]: items.map((m) => (m.id === memory.id ? memory : m)),
        };
      }),
    [],
  );

  const value = useMemo(
    () => ({
      byMessage,
      setLearnedForMessage,
      clearLearnedMemories,
      removeLearnedMemory,
      updateLearnedMemory,
    }),
    [
      byMessage,
      setLearnedForMessage,
      clearLearnedMemories,
      removeLearnedMemory,
      updateLearnedMemory,
    ],
  );

  return (
    <LearnedMemoriesContext.Provider value={value}>
      {children}
    </LearnedMemoriesContext.Provider>
  );
}

export function useLearnedMemories() {
  const ctx = useContext(LearnedMemoriesContext);
  if (!ctx) {
    throw new Error(
      "useLearnedMemories must be used within LearnedMemoriesProvider",
    );
  }
  return ctx;
}
