import type { Config } from "tailwindcss";

const hsl = (name: string) => `hsl(var(--${name}) / <alpha-value>)`;

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", '"SF Pro Text"', '"Helvetica Neue"', "sans-serif"],
        brand: ["Poppins", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      // AppKit's sizes: 13 px body, 11 px secondary
      fontSize: {
        xs: ["11px", "14px"],
        sm: ["13px", "18px"],
        base: ["13px", "18px"],
        lg: ["15px", "20px"],
        xl: ["17px", "22px"],
        "2xl": ["22px", "28px"],
      },
      colors: {
        background: hsl("background"),
        foreground: hsl("foreground"),
        card: { DEFAULT: hsl("card"), foreground: hsl("card-foreground") },
        popover: { DEFAULT: hsl("popover"), foreground: hsl("popover-foreground") },
        primary: { DEFAULT: hsl("primary"), foreground: hsl("primary-foreground") },
        secondary: { DEFAULT: hsl("secondary"), foreground: hsl("secondary-foreground") },
        muted: { DEFAULT: hsl("muted"), foreground: hsl("muted-foreground") },
        accent: { DEFAULT: hsl("accent"), foreground: hsl("accent-foreground") },
        destructive: { DEFAULT: hsl("destructive"), foreground: hsl("destructive-foreground") },
        border: hsl("border"),
        input: hsl("input"),
        ring: hsl("ring"),
        brand: hsl("brand-accent"),
        success: hsl("success"),
        warning: hsl("warning"),
        info: hsl("info"),
        window: hsl("window"),
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      boxShadow: { elevated: "0 4px 12px rgba(0, 0, 0, 0.15)" },
    },
  },
  plugins: [],
} satisfies Config;
