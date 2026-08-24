import "../styles/globals.css";
import type { Metadata } from "next";
import Waves from "@/components/Waves";

export const metadata: Metadata = {
  title: "OpsBrain",
  description: "Industrial Knowledge Intelligence Platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div className="fixed top-0 left-0 w-screen h-screen z-[-1]">
          <Waves lineColor="#4A1B26" backgroundColor="transparent" />
        </div>
        {children}
      </body>
    </html>
  );
}
