import { motion } from 'framer-motion';
import type { Session } from '../types';
import { Menu, Settings, Hash, Sparkles, Download } from 'lucide-react';

interface Props {
  session: Session | null;
  onToggleSidebar: () => void;
  onOpenSettings: () => void;
  onExport: () => void;
  provider?: 'qwen' | 'claude';
  model?: string;
  onProviderChange?: (provider: 'qwen' | 'claude') => void;
  onModelChange?: (model: string) => void;
}

export default function ChatHeader({
  session,
  onToggleSidebar,
  onOpenSettings,
  onExport,
  provider = 'qwen',
  model = 'sonnet',
  onProviderChange,
  onModelChange,
}: Props) {
  return (
    <header className="h-14 flex-shrink-0 glass border-b border-border/50 flex items-center justify-between px-4 z-10 relative">
      {/* Subtle top gradient line */}
      <div className="absolute bottom-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-accent/20 to-transparent" />

      <div className="flex items-center gap-3 min-w-0">
        <button
          onClick={onToggleSidebar}
          className="lg:hidden p-2 rounded-xl hover:bg-bg-hover text-text-secondary transition-all duration-200 hover:text-text-primary active:scale-95"
        >
          <Menu size={20} />
        </button>
        <div className="flex items-center gap-2.5 min-w-0">
          {session ? (
            <motion.div
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              className="w-6 h-6 rounded-lg bg-accent/10 border border-accent/20 flex items-center justify-center flex-shrink-0"
            >
              <Hash size={12} className="text-accent" />
            </motion.div>
          ) : (
            <div className="w-6 h-6 rounded-lg bg-bg-tertiary flex items-center justify-center flex-shrink-0">
              <Sparkles size={12} className="text-text-muted" />
            </div>
          )}
          <h2 className="text-sm font-semibold text-text-primary truncate">
            {session?.title || 'Выберите чат'}
          </h2>
        </div>
      </div>

      {session && (
        <div className="flex items-center gap-2">
          {/* Provider selector */}
          <select
            value={provider}
            onChange={(e) => onProviderChange?.(e.target.value as 'qwen' | 'claude')}
            className="px-2 py-1 text-xs rounded-lg bg-bg-primary/80 border border-border/60 text-text-primary outline-none focus:border-accent/40 transition-all duration-200"
          >
            <option value="qwen">Qwen</option>
            <option value="claude">Claude</option>
          </select>

          {/* Model selector (only for Claude) */}
          {provider === 'claude' && (
            <select
              value={model}
              onChange={(e) => onModelChange?.(e.target.value)}
              className="px-2 py-1 text-xs rounded-lg bg-bg-primary/80 border border-border/60 text-text-primary outline-none focus:border-accent/40 transition-all duration-200"
            >
              <option value="opus">Opus</option>
              <option value="sonnet">Sonnet</option>
              <option value="haiku">Haiku</option>
            </select>
          )}

          <motion.button
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            onClick={onExport}
            className="p-2 rounded-xl hover:bg-bg-hover text-text-secondary hover:text-text-primary transition-all duration-200 active:scale-90"
            title="Экспорт в Markdown"
          >
            <Download size={17} />
          </motion.button>
          <motion.button
            initial={{ opacity: 0, rotate: -180 }}
            animate={{ opacity: 1, rotate: 0 }}
            transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
            onClick={onOpenSettings}
            className="p-2 rounded-xl hover:bg-bg-hover text-text-secondary hover:text-text-primary transition-all duration-300 hover:rotate-90 active:scale-90"
            title="Настройки"
          >
            <Settings size={17} />
          </motion.button>
        </div>
      )}
    </header>
  );
}
