"use client";

import { useEffect } from "react";
import { motion } from "motion/react";

import { CopilotSidebar } from "@/components/workspace/copilot-sidebar";
import { LabPage } from "@/components/workspace/lab/lab-page";
import { MobileWorkspace } from "@/components/workspace/mobile-workspace";
import { NotificationBanner } from "@/components/workspace/notification-banner";
import { WorkspaceGrid } from "@/components/workspace/workspace-grid";
import { WorkspaceToolbar } from "@/components/workspace/workspace-toolbar";
import { TERMINAL_COLLAPSED_HEIGHT } from "@/lib/workspace/use-terminal-store";
import { useCopilotStore } from "@/lib/workspace/use-copilot-store";
import { useIsMobile } from "@/lib/workspace/use-is-mobile";
import { usePageHotkeys } from "@/lib/workspace/use-page-hotkeys";
import { useVersionCheck } from "@/lib/workspace/use-version-check";
import { motionStd } from "@/lib/workspace/motion";
import { isLabHash } from "@/components/workspace/lab/lab-nav";
import {
  LAB_PAGE_ID,
  useWorkspaceStore,
} from "@/lib/workspace/use-workspace-store";

// Rendered under AppShell's WorkspaceToastProvider so the version-skew check
// can toast.
function VersionCheck() {
  useVersionCheck();
  return null;
}

export default function WorkspacePageImpl() {
  usePageHotkeys();

  const labActive = useWorkspaceStore((s) => s.activePageId === LAB_PAGE_ID);
  const setActivePage = useWorkspaceStore((s) => s.setActivePage);

  // Deep-link entry: a `#lab...` hash (e.g. shared from another surface) opens
  // the Lab. Also honored if the hash is set while the app is already open.
  useEffect(() => {
    function maybeOpenLab() {
      if (isLabHash(window.location.hash)) {
        setActivePage(LAB_PAGE_ID);
      }
    }
    maybeOpenLab();
    window.addEventListener("hashchange", maybeOpenLab);
    return () => window.removeEventListener("hashchange", maybeOpenLab);
  }, [setActivePage]);

  const isMobile = useIsMobile();
  const collapsedHeight = TERMINAL_COLLAPSED_HEIGHT;
  const open = useCopilotStore((s) => s.open);
  const pinned = useCopilotStore((s) => s.pinned);
  const width = useCopilotStore((s) => s.width);
  const hydrate = useCopilotStore((s) => s.hydrate);
  const toggleCopilot = useCopilotStore((s) => s.toggle);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  // Ctrl/Cmd+J toggles the copilot open/closed — doesn't collide with the
  // command console's Ctrl/Cmd+K or the Alt+1-5 / Alt+[ / ] page hotkeys.
  useEffect(() => {
    if (isMobile) return;

    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.defaultPrevented || event.repeat) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "j") {
        event.preventDefault();
        toggleCopilot();
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isMobile, toggleCopilot]);

  // Only a PINNED (docked) copilot should push the grid over — a floating
  // copilot overlays the grid instead, so it must not reserve layout space.
  const dockedPadding = open && pinned ? width : 0;

  // Phones get a purpose-built bottom-tab shell instead of the desktop widget
  // grid. The chat providers now live in AppShell (so chat persists across
  // routes) — the Agent tab's thread reads the same shared runtime.
  if (isMobile) {
    return (
      <>
        <VersionCheck />
        <MobileWorkspace />
      </>
    );
  }

  return (
    <>
      <VersionCheck />
      <div className="flex h-svh min-h-[640px] flex-col overflow-hidden bg-background">
        <WorkspaceToolbar />
        <NotificationBanner />

        {/* paddingBottom reserves the collapsed terminal strip's height — the
            Terminal itself is now a fixed viewport overlay mounted in AppShell. */}
        <div
          className="relative flex min-h-0 flex-1 flex-col"
          style={{ paddingBottom: collapsedHeight }}
        >
          <div id="workspace-grid-host" className="relative min-h-0 flex-1">
            <motion.div
              className="h-full min-h-0"
              initial={false}
              animate={{ paddingRight: dockedPadding }}
              transition={motionStd}
            >
              {/* The Lab is a built-in view (LAB_PAGE_ID sentinel): it renders
                  full-bleed here in place of the widget grid, inside the same
                  shell so the toolbar + terminal strip stay put. */}
              {labActive ? <LabPage /> : <WorkspaceGrid />}
            </motion.div>
            <CopilotSidebar />
          </div>
        </div>
      </div>
    </>
  );
}
