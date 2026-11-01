/** @type {import('tailwindcss').Config} */
// v10「朱批」：组件里零散写死的 Tailwind 色阶映射到版面色——蓝 → 朱砂、灰/石板 → 暖灰、
// 绿 → 石绿、琥珀 → 赭石、红/玫红 → 绛红；圆角与阴影收到纸面尺度（细线分隔，不靠投影浮起卡片）。
const cinnabar = {
  50: '#fbf1ec', 100: '#f6ddd2', 200: '#ecb9a6', 300: '#df8f74', 400: '#d06a4b',
  500: '#c24d2d', 600: '#b5361c', 700: '#962b16', 800: '#752313', 900: '#561b10', 950: '#2f0e08',
}
const warmGray = {
  50: '#f7f5f0', 100: '#efebe3', 200: '#e2dccf', 300: '#cdc5b5', 400: '#a59d8e',
  500: '#7a746a', 600: '#5c574f', 700: '#45413b', 800: '#2d2b27', 900: '#1c1b18', 950: '#121110',
}
const malachite = {
  50: '#f1f6f2', 100: '#dce9e0', 200: '#b9d3c2', 300: '#8fb79e', 400: '#64977a',
  500: '#4a8063', 600: '#3d7457', 700: '#305c45', 800: '#254836', 900: '#1b3527', 950: '#0f1f17',
}
const ochre = {
  50: '#faf5ea', 100: '#f2e6c9', 200: '#e6cd93', 300: '#d8b05e', 400: '#c8943a',
  500: '#b6822a', 600: '#a8741b', 700: '#875c16', 800: '#674613', 900: '#4a320f', 950: '#2a1c08',
}
const crimson = {
  50: '#faf0ef', 100: '#f3d9d6', 200: '#e6b1ab', 300: '#d6857c', 400: '#c45d53',
  500: '#b3443a', 600: '#a3302a', 700: '#852621', 800: '#661e1a', 900: '#4a1613', 950: '#2a0c0a',
}

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: ['selector', '[data-mode="dark"]'],
  theme: {
    extend: {
      colors: {
        blue: cinnabar, indigo: cinnabar, sky: cinnabar,
        gray: warmGray, slate: warmGray, zinc: warmGray, neutral: warmGray, stone: warmGray,
        green: malachite, emerald: malachite,
        amber: ochre, yellow: ochre, orange: ochre,
        red: crimson, rose: crimson,
      },
      fontFamily: {
        sans: ['Instrument Sans Variable', '-apple-system', 'BlinkMacSystemFont', 'PingFang SC', 'sans-serif'],
        serif: ['Newsreader Variable', 'Noto Serif SC Variable', 'Songti SC', 'Georgia', 'serif'],
        mono: ['JetBrains Mono Variable', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      transitionTimingFunction: {
        spring: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
      },
      borderRadius: {
        seed: 'var(--seed-radius, 2px)',
        sm: '2px', DEFAULT: '2px', md: '2px', lg: '3px', xl: '3px', '2xl': '4px', '3xl': '4px',
      },
      boxShadow: {
        '2xs': 'none', xs: 'none', sm: 'none', DEFAULT: 'none', md: 'none',
        lg: '0 1px 0 var(--border), 0 12px 32px -18px rgba(28, 27, 24, 0.28)',
        xl: '0 1px 0 var(--border), 0 18px 40px -20px rgba(28, 27, 24, 0.32)',
        '2xl': '0 1px 0 var(--border), 0 24px 56px -24px rgba(28, 27, 24, 0.38)',
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
}
