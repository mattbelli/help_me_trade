import { Inter, JetBrains_Mono } from "next/font/google"
import { Suspense } from "react"

import "./globals.css"
import { AppShell } from "@/components/layout/app-shell"
import { ThemeProvider } from "@/components/theme-provider"
import { TooltipProvider } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

const plexSans = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans",
})

const plexMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-mono",
})

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={cn(
        "antialiased",
        plexMono.variable,
        "font-sans",
        plexSans.variable,
      )}
    >
      <head>
        {/* Anti-FOUC: inject saved custom-theme rules before next-themes (body)
            applies data-theme, so a custom theme renders without a flash. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var raw=localStorage.getItem("hmt-custom-themes-v1");if(!raw)return;var themes=JSON.parse(raw);if(!Array.isArray(themes))return;var css=themes.map(function(t){if(!t||!t.id||!t.vars)return"";var decls=Object.keys(t.vars).map(function(k){return k+":"+t.vars[k]+";"}).join("");return '[data-theme="'+t.id+'"]{color-scheme:'+(t.colorScheme||"dark")+";"+decls+"}"}).join("\\n");var el=document.getElementById("hmt-custom-themes");if(!el){el=document.createElement("style");el.id="hmt-custom-themes";document.head.appendChild(el)}el.textContent=css}catch(e){}})();`,
          }}
        />
      </head>
      <body>
        <ThemeProvider>
          <TooltipProvider>
            <Suspense fallback={null}>
              <AppShell>{children}</AppShell>
            </Suspense>
          </TooltipProvider>
        </ThemeProvider>
      </body>
    </html>
  )
}
