/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 950: "#070B14", 900: "#0B1220", 850: "#0F1828", 800: "#141E31", 700: "#1C2840", 600: "#283553", 500: "#3A4868" },
        fog: { 100: "#E8EDF5", 200: "#C9D2E1", 300: "#9AA7BD", 400: "#717F97" },
        teal: { 300: "#5EEAD4", 400: "#2DD4BF", 500: "#14B8A6", 900: "#0B3B3A" },
        amber: { 300: "#FCD34D", 400: "#F5A524", 900: "#3D2A0A" },
        coral: { 400: "#F87171", 500: "#EF4444", 900: "#3B1214" },
        sky: { 400: "#60A5FA" },
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
        display: ['"Space Grotesk"', '"IBM Plex Sans"', "sans-serif"],
      },
    },
  },
  plugins: [],
};
