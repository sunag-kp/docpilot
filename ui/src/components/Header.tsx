interface Props {
  mode: "live" | "demo";
  email: string;
  onSignOut: () => void;
}

export function Logo() {
  return (
    <svg viewBox="0 0 32 32" className="h-7 w-7" aria-hidden>
      <rect width="32" height="32" rx="7" fill="#4f46e5" />
      <path d="M9 8h8a7 7 0 0 1 0 14h-3v4H9z" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinejoin="round" />
      <circle cx="16.5" cy="15" r="2.2" fill="#fff" />
    </svg>
  );
}

export function Header({ mode, email, onSignOut }: Props) {
  return (
    <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-2.5">
      <div className="flex items-center gap-2.5">
        <Logo />
        <span className="text-lg font-semibold tracking-tight">DocPilot</span>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
          {mode === "demo" ? "browser demo" : "live"}
        </span>
      </div>
      <div className="flex items-center gap-3 text-sm text-slate-600">
        <span className="hidden sm:inline">{email}</span>
        {mode === "live" && (
          <button onClick={onSignOut} className="rounded-md px-2 py-1 hover:bg-slate-100 hover:text-slate-900">
            Sign out
          </button>
        )}
      </div>
    </header>
  );
}
