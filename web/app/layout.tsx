import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Microsoft GEO Optimizer",
    template: "%s | Microsoft GEO Optimizer",
  },
  description: "Project-scoped GEO optimization workspace.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en-GB">
      <body>{children}</body>
    </html>
  );
}
