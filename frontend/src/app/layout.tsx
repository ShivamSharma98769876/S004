import type { Metadata } from "next";
import type { ReactNode } from "react";
import AuthGuard from "@/components/AuthGuard";
import "./globals.css";

export const metadata: Metadata = {
  title: "S004 Frontend",
  description: "Strategy marketplace and dashboard",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body suppressHydrationWarning>
        <div className="app-shell">
          <AuthGuard>{children}</AuthGuard>
        </div>
      </body>
    </html>
  );
}
