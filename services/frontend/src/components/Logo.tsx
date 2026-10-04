import { Flame } from 'lucide-react';

export function Logo({ size = 'md', showText = true, className = '' }: {
  size?: 'sm' | 'md' | 'lg'; showText?: boolean; className?: string;
}) {
  const large = size === 'lg';
  return (
    <div className={`inline-flex items-center gap-2.5 ${className}`}>
      <span className={`flex shrink-0 items-center justify-center rounded-xl bg-amber-400 text-slate-950 ${large ? 'h-12 w-12' : 'h-9 w-9'}`}>
        <Flame aria-hidden="true" className={large ? 'h-7 w-7' : 'h-5 w-5'} />
      </span>
      {showText && <span className="min-w-0 leading-tight">
        <span className={`block font-semibold tracking-tight ${large ? 'text-xl' : 'text-sm'}`}>Commonflame</span>
        {size !== 'sm' && <span className="block text-[11px] opacity-60">A shared workspace for people and agents</span>}
      </span>}
    </div>
  );
}
