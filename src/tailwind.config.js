/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        'bg-elev': 'var(--bg-elev)',
        surface: 'var(--surface)',
        'surface-2': 'var(--surface-2)',
        'surface-3': 'var(--surface-3)',
        line: 'var(--border)',
        'line-strong': 'var(--border-strong)',
        ink: 'var(--text)',
        'ink-dim': 'var(--text-dim)',
        'ink-faint': 'var(--text-faint)',
        accent: 'var(--accent)',
        'accent-hover': 'var(--accent-hover)',
        'accent-ink': 'var(--accent-contrast)',
        'accent-soft': 'var(--accent-soft)',
        'accent-line': 'var(--accent-border)',
        ok: 'var(--success)',
        warn: 'var(--warning)',
        bad: 'var(--danger)',
        'bad-soft': 'var(--danger-soft)',
      },
      fontFamily: {
        sans: ['var(--font-sans)'],
        mono: ['var(--font-mono)'],
      },
      borderRadius: {
        DEFAULT: 'var(--r-sm)',
        md: 'var(--r-sm)',
        lg: 'var(--r-md)',
        xl: 'var(--r-lg)',
      },
      boxShadow: {
        panel: 'var(--shadow-panel)',
        pop: 'var(--shadow-pop)',
        none: 'none',
      },
      transitionTimingFunction: {
        out: 'var(--ease-out)',
        'in-out': 'var(--ease-in-out)',
      },
      transitionDuration: {
        150: '150ms',
        200: '200ms',
        240: '240ms',
      },
      keyframes: {
        'rise-in': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'pop-in': {
          from: { opacity: '0', transform: 'scale(0.95)' },
          to: { opacity: '1', transform: 'scale(1)' },
        },
        'drop-in': {
          from: { opacity: '0', transform: 'scale(0.95)' },
          to: { opacity: '1', transform: 'scale(1)' },
        },
        'log-in': {
          from: { opacity: '0', transform: 'translateY(3px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'sweep': {
          from: { transform: 'translateX(-100%)' },
          to: { transform: 'translateX(320%)' },
        },
        'pulse-dim': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.42' },
        },
      },
      animation: {
        'rise-in': 'rise-in 240ms var(--ease-out) both',
        'pop-in': 'pop-in 200ms var(--ease-out) both',
        'drop-in': 'drop-in 180ms var(--ease-out) both',
        'log-in': 'log-in 200ms var(--ease-out) both',
        sweep: 'sweep 1400ms var(--ease-in-out) infinite',
        'pulse-dim': 'pulse-dim 1400ms var(--ease-in-out) infinite',
      },
    },
  },
  plugins: [],
}
