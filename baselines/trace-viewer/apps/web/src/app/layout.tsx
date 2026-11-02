import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "IdeaEval Trace Viewer",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="h-screen overflow-hidden antialiased">{children}</body>
    </html>
  );
}
