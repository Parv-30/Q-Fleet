import { Anchor, Moon, Sun } from 'lucide-react';

interface HeaderProps {
  isDark: boolean;
  onToggleDark: () => void;
}

export default function Header({ isDark, onToggleDark }: HeaderProps) {
  return (
    <header className="border-b border-border bg-card">
      <div className="mx-auto flex max-w-[1600px] items-center justify-between gap-4 px-4 py-4 sm:px-6">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <Anchor size={22} aria-hidden="true" />
          </span>
          <div>
            <h1 className="text-lg font-semibold leading-tight text-foreground sm:text-xl">
              Q-Fleet AI
            </h1>
            <p className="text-xs text-muted-foreground sm:text-sm">
              Multi-objective voyage optimization &amp; Pareto-front trade-off analysis
            </p>
          </div>
        </div>

        <button
          type="button"
          onClick={onToggleDark}
          aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
          aria-pressed={isDark}
          className="flex h-10 w-10 shrink-0 cursor-pointer items-center justify-center rounded-lg border border-border bg-background text-foreground transition-colors duration-200 hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-card"
        >
          {isDark ? <Sun size={18} aria-hidden="true" /> : <Moon size={18} aria-hidden="true" />}
        </button>
      </div>
    </header>
  );
}
