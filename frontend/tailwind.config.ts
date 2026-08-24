import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Single-accent palette (crimson + black + white). Burnt Crimson is
        // the ONLY accent color - never introduce a second hue for "variety".
        void: "#050204", // Abyssal Void - background base
        carbon: "#12080A", // Forged Carbon - surface primary
        maroon: "#240F14", // Deep Maroon - surface secondary
        crimson: "#D91656", // Burnt Crimson - the one accent
        "wave-line": "#4A1B26", // Waves component line color (not a UI color)
        stark: "#FDFCFD", // Stark White - text primary
        "ash-rose": "#A68A91", // Ash Rose - text secondary, muted accent tint
        status: "#FF5500", // status/error ONLY - never a design accent
      },
    },
  },
  plugins: [],
};

export default config;
