/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      "colors": {
        "error-container": "#93000a",
        "surface-container": "#171f33",
        "on-tertiary": "#2d2a5b",
        "secondary-fixed-dim": "#4cd7f6",
        "on-primary-fixed": "#23005c",
        "inverse-on-surface": "#283044",
        "surface-container-lowest": "#060e20",
        "surface-bright": "#31394d",
        "error": "#ffb4ab",
        "surface-container-low": "#131b2e",
        "surface": "#0b1326",
        "outline": "#958ea0",
        "tertiary-fixed": "#e3dfff",
        "on-primary": "#3c0091",
        "inverse-surface": "#dae2fd",
        "on-secondary": "#003640",
        "on-background": "#dae2fd",
        "on-primary-fixed-variant": "#5516be",
        "on-error": "#690005",
        "inverse-primary": "#6d3bd7",
        "on-tertiary-fixed-variant": "#444173",
        "surface-variant": "#2d3449",
        "on-tertiary-fixed": "#181445",
        "surface-container-high": "#222a3d",
        "on-tertiary-container": "#262354",
        "surface-tint": "#d0bcff",
        "tertiary-container": "#8e8bc2",
        "on-primary-container": "#340080",
        "secondary": "#4cd7f6",
        "primary": "#d0bcff",
        "on-secondary-fixed": "#001f26",
        "on-error-container": "#ffdad6",
        "on-secondary-fixed-variant": "#004e5c",
        "background": "#0b1326",
        "tertiary": "#c4c1fb",
        "primary-fixed-dim": "#d0bcff",
        "secondary-fixed": "#acedff",
        "on-secondary-container": "#00424e",
        "outline-variant": "#494454",
        "surface-dim": "#0b1326",
        "on-surface": "#dae2fd",
        "tertiary-fixed-dim": "#c4c1fb",
        "primary-container": "#a078ff",
        "on-surface-variant": "#cbc3d7",
        "secondary-container": "#03b5d3",
        "surface-container-highest": "#2d3449",
        "primary-fixed": "#e9ddff"
      },
      "borderRadius": {
        "DEFAULT": "0.25rem",
        "lg": "0.5rem",
        "xl": "0.75rem",
        "full": "9999px"
      },
      "spacing": {
        "unit": "4px",
        "orb-size-lg": "240px",
        "gutter": "1.5rem",
        "orb-size-sm": "48px",
        "container-padding": "2rem"
      },
      "fontFamily": {
        "headline-md-mobile": ["Geist", "sans-serif"],
        "metadata-sm": ["JetBrains Mono", "monospace"],
        "body-lg": ["Inter", "sans-serif"],
        "headline-md": ["Geist", "sans-serif"],
        "body-md": ["Inter", "sans-serif"],
        "display-lg": ["Geist", "sans-serif"],
        "label-caps": ["JetBrains Mono", "monospace"],
        "geist": ["Geist", "sans-serif"]
      },
      "fontSize": {
        "headline-md-mobile": ["24px", { "lineHeight": "32px", "fontWeight": "500" }],
        "metadata-sm": ["12px", { "lineHeight": "16px", "letterSpacing": "0.05em", "fontWeight": "500" }],
        "body-lg": ["18px", { "lineHeight": "28px", "fontWeight": "400" }],
        "headline-md": ["32px", { "lineHeight": "40px", "letterSpacing": "-0.01em", "fontWeight": "500" }],
        "body-md": ["16px", { "lineHeight": "24px", "fontWeight": "400" }],
        "display-lg": ["48px", { "lineHeight": "56px", "letterSpacing": "-0.02em", "fontWeight": "600" }],
        "label-caps": ["10px", { "lineHeight": "12px", "letterSpacing": "0.1em", "fontWeight": "700" }]
      }
    }
  },
  plugins: [],
}
