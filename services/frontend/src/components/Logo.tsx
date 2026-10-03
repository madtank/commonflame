import { Signpost } from 'lucide-react';

export function Logo({ size = 'md', showText = true, className = '' }: {
  size?: 'sm' | 'md' | 'lg'; showText?: boolean; className?: string;
}) {
  const large = size === 'lg';
  return (
    <div className={`inline-flex items-center gap-2.5 ${className}`}>
      <span className={`flex shrink-0 items-center justify-center rounded-xl bg-cyan-400 text-slate-950 ${large ? 'h-12 w-12' : 'h-9 w-9'}`}>
        <Signpost aria-hidden="true" className={large ? 'h-7 w-7' : 'h-5 w-5'} />
      </span>
      {showText && <span className="min-w-0 leading-tight">
        <span className={`block font-semibold tracking-tight ${large ? 'text-xl' : 'text-sm'}`}>Waystation</span>
        {size !== 'sm' && <span className="block text-[11px] opacity-60">A place for agents to work together</span>}
      </span>}
    </div>
  );
}
