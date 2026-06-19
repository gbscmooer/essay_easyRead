/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: [
          'Inter Variable',
          'Inter',
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'sans-serif',
        ],
      },
      transitionTimingFunction: {
        spring: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
      },
      borderRadius: {
        seed: 'var(--seed-radius, 12px)',
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
}
