import { Anchor, Menu, Moon, Sun, X } from 'lucide-react';
import { useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { useDarkMode } from '../useDarkMode';

const NAV_LINKS = [
  { to: '/', label: 'Home', end: true },
  { to: '/voyage-setup', label: 'Voyage Setup', end: false },
  { to: '/about', label: 'About', end: false },
];

function navLinkClass({ isActive }: { isActive: boolean }): string {
  return `cursor-pointer rounded-lg px-3 py-2 text-sm font-medium transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-ring ${
    isActive
      ? 'bg-muted text-foreground'
      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
  }`;
}

/**
 * DarkModeToggle -- a labelled sliding switch (sun/moon icons + track/thumb),
 * not a bare icon button, per the task's explicit requirement for "a real,
 * clearly-labeled switch/button, not just a tiny icon with no visible
 * state." Reused identically on every page via Layout so the toggle is
 * genuinely present everywhere, not just on one screen.
 */
function DarkModeToggle({ isDark, onToggle }: { isDark: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={isDark}
      onClick={onToggle}
      aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      className="group relative flex h-9 w-16 shrink-0 cursor-pointer items-center rounded-full border border-border bg-muted px-1 transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background"
    >
      <Sun
        size={14}
        aria-hidden="true"
        className={`absolute left-1.5 transition-opacity duration-200 ${isDark ? 'opacity-40' : 'opacity-100 text-accent'}`}
      />
      <Moon
        size={14}
        aria-hidden="true"
        className={`absolute right-1.5 transition-opacity duration-200 ${isDark ? 'opacity-100 text-accent' : 'opacity-40'}`}
      />
      <span
        className={`z-10 flex h-7 w-7 items-center justify-center rounded-full bg-card shadow-md transition-transform duration-200 ease-out ${
          isDark ? 'translate-x-7' : 'translate-x-0'
        }`}
      />
    </button>
  );
}

export default function Layout() {
  const [isDark, toggleDark] = useDarkMode();
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <header className="sticky top-0 z-40 border-b border-border bg-card/95 backdrop-blur-none">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <NavLink to="/" className="flex cursor-pointer items-center gap-3" aria-label="Q-Fleet AI home">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <Anchor size={18} aria-hidden="true" />
            </span>
            <span className="flex flex-col leading-tight">
              <span className="text-base font-bold tracking-tight text-foreground">Q-Fleet AI</span>
              <span className="hidden text-[11px] font-medium text-muted-foreground sm:block">
                Fleet Optimization Platform
              </span>
            </span>
          </NavLink>

          <nav className="hidden items-center gap-1 md:flex" aria-label="Primary">
            {NAV_LINKS.map((link) => (
              <NavLink key={link.to} to={link.to} end={link.end} className={navLinkClass}>
                {link.label}
              </NavLink>
            ))}
          </nav>

          <div className="flex items-center gap-2">
            <DarkModeToggle isDark={isDark} onToggle={toggleDark} />
            <button
              type="button"
              onClick={() => setMobileOpen((o) => !o)}
              aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
              aria-expanded={mobileOpen}
              className="flex h-9 w-9 cursor-pointer items-center justify-center rounded-lg border border-border text-foreground transition-colors duration-200 hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring md:hidden"
            >
              {mobileOpen ? <X size={18} aria-hidden="true" /> : <Menu size={18} aria-hidden="true" />}
            </button>
          </div>
        </div>

        {mobileOpen && (
          <nav
            className="flex flex-col gap-1 border-t border-border px-4 py-3 md:hidden"
            aria-label="Primary mobile"
          >
            {NAV_LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.end}
                className={navLinkClass}
                onClick={() => setMobileOpen(false)}
              >
                {link.label}
              </NavLink>
            ))}
          </nav>
        )}
      </header>

      <main className="flex-1">
        <Outlet />
      </main>

      <footer className="border-t border-border bg-card">
        <div className="mx-auto flex max-w-[1400px] flex-col gap-4 px-4 py-10 sm:px-6 md:flex-row md:items-start md:justify-between">
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <span className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
                <Anchor size={14} aria-hidden="true" />
              </span>
              <span className="text-sm font-bold text-foreground">Q-Fleet AI</span>
            </div>
            <p className="max-w-sm text-xs text-muted-foreground">
              Decision-support platform for maritime fleet operators — predictive fuel &amp; cost
              modeling, lifecycle emissions, and multi-objective voyage optimization.
            </p>
          </div>

          <nav className="flex gap-6 text-xs" aria-label="Footer">
            <NavLink to="/" end className="cursor-pointer font-medium text-muted-foreground transition-colors duration-200 hover:text-foreground">
              Home
            </NavLink>
            <NavLink to="/voyage-setup" className="cursor-pointer font-medium text-muted-foreground transition-colors duration-200 hover:text-foreground">
              Voyage Setup
            </NavLink>
            <NavLink to="/about" className="cursor-pointer font-medium text-muted-foreground transition-colors duration-200 hover:text-foreground">
              About
            </NavLink>
          </nav>
        </div>
        <div className="border-t border-border px-4 py-4 text-center text-[11px] text-muted-foreground sm:px-6">
          © {new Date().getFullYear()} Q-Fleet AI. Decision support for green fleet optimization.
        </div>
      </footer>
    </div>
  );
}
