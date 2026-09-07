import { AlertTriangle, RotateCcw } from 'lucide-react';

interface ErrorStateProps {
  message: string;
  onRetry: () => void;
}

export default function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 rounded-xl border border-destructive/40 bg-destructive/10 p-8 text-center shadow-sm"
    >
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive text-[color:var(--color-on-destructive)]">
        <AlertTriangle size={24} aria-hidden="true" />
      </span>
      <h2 className="text-base font-semibold text-foreground">Optimization request failed</h2>
      <p className="max-w-md text-sm text-muted-foreground">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-1 flex cursor-pointer items-center gap-2 rounded-lg border-2 border-primary px-5 py-2 font-semibold text-primary transition-all duration-200 hover:bg-primary hover:text-primary-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
      >
        <RotateCcw size={16} aria-hidden="true" />
        Retry
      </button>
    </div>
  );
}
