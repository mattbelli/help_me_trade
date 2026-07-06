import { createContext, useContext } from "react";

// True when the workspace page a widget lives on is the active (visible) one.
// All pages stay mounted (see WorkspacePageGrid); widgets that run motion layout
// animations read this to SUPPRESS animating while hidden — otherwise the
// hidden→visible position swap (absolute/top-left → in-flow) makes motion slide
// their contents in on every page switch.
export const PageActiveContext = createContext(true);

export function usePageActive(): boolean {
  return useContext(PageActiveContext);
}
