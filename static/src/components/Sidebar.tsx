import { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import type { Session } from '../types';
import {
  Plus,
  MessageSquare,
  Trash2,
  X,
  Sparkles,
  Search,
} from 'lucide-react';

interface Props {
  sessions: Session[];
  currentSession: Session | null;
  isOpen: boolean;
  onClose: () => void;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
}

export default function Sidebar({
  sessions,
  currentSession,
  isOpen,
  onClose,
  onSelect,
  onCreate,
  onDelete,
}: Props) {
  const [search, setSearch] = useState('');
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const sidebarRef = useRef<HTMLDivElement>(null);

  const filtered = sessions.filter((s) =>
    s.title.toLowerCase().includes(search.toLowerCase())
  );

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (isOpen && sidebarRef.current && !sidebarRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen, onClose]);

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    setDeletingId(id);
    setTimeout(() => {
      onDelete(id);
      setDeletingId(null);
    }, 350);
  };

  const pluralize = (n: number) => {
    if (n === 1) return 'чат';
    if (n >= 2 && n <= 4) return 'чата';
    return 'чатов';
  };

  return (
    <>
      {/* Mobile overlay */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25 }}
            className="fixed inset-0 bg-black/60 backdrop-blur-md z-40 lg:hidden"
            onClick={onClose}
          />
        )}
      </AnimatePresence>

      <aside
        ref={sidebarRef}
        className={`fixed lg:relative z-50 h-full w-[280px] glass border-r border-border/50 flex flex-col transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] lg:translate-x-0 ${
          isOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        {/* Header */}
        <div className="p-4 border-b border-border/50">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2.5">
              <motion.div
                className="w-9 h-9 rounded-xl bg-gradient-to-br from-accent via-purple to-cyan flex items-center justify-center shadow-lg shadow-accent/20 relative overflow-hidden"
                whileHover={{ scale: 1.1, rotate: 5 }}
                whileTap={{ scale: 0.95 }}
                transition={{ type: 'spring', stiffness: 400, damping: 15 }}
              >
                {/* Shimmer overlay */}
                <motion.div
                  className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent"
                  animate={{ x: ['-100%', '100%'] }}
                  transition={{ duration: 3, repeat: Infinity, repeatDelay: 2 }}
                />
                <Sparkles size={16} className="text-white relative z-10" />
              </motion.div>
              <div>
                <h1 className="text-base font-bold text-text-primary tracking-tight">
                  Thule UI
                </h1>
                <p className="text-[10px] text-text-muted">AI Assistant</p>
              </div>
            </div>
            <button
              onClick={onClose}
              className="lg:hidden p-1.5 rounded-lg hover:bg-bg-hover text-text-secondary transition-all duration-200 hover:text-text-primary hover:rotate-90"
            >
              <X size={18} />
            </button>
          </div>

          <motion.button
            onClick={onCreate}
            className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl bg-gradient-to-r from-accent to-accent-dark hover:from-accent-dark hover:to-accent text-white font-medium transition-all duration-300 hover:shadow-lg hover:shadow-accent/25 active:scale-[0.97] group relative overflow-hidden"
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.97 }}
          >
            {/* Shimmer */}
            <motion.div
              className="absolute inset-0 bg-gradient-to-r from-transparent via-white/10 to-transparent"
              animate={{ x: ['-100%', '100%'] }}
              transition={{ duration: 2.5, repeat: Infinity, repeatDelay: 3 }}
            />
            <Plus size={18} className="transition-transform duration-300 group-hover:rotate-90 relative z-10" />
            <span className="relative z-10">Новый чат</span>
          </motion.button>
        </div>

        {/* Search */}
        <AnimatePresence>
          {sessions.length > 3 && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="px-3 pt-3 overflow-hidden"
            >
              <div className="relative">
                <Search
                  size={14}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted"
                />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Поиск..."
                  className="w-full py-2.5 pl-9 pr-3 text-sm rounded-xl bg-bg-primary/80 border border-border text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent/10 transition-all duration-200"
                />
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Sessions list */}
        <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
          {filtered.length === 0 && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="text-center py-16 text-text-muted text-sm"
            >
              {search ? 'Ничего не найдено' : 'Нет чатов'}
            </motion.div>
          )}
          <AnimatePresence mode="popLayout">
            {filtered.map((session, index) => {
              const isActive = currentSession?.id === session.id;
              const isDeleting = deletingId === session.id;
              const isHovered = hoveredId === session.id;
              return (
                <motion.div
                  key={session.id}
                  layout
                  initial={{ opacity: 0, y: -10, scale: 0.95 }}
                  animate={{
                    opacity: isDeleting ? 0 : 1,
                    y: 0,
                    scale: isDeleting ? 0.8 : 1,
                    x: isDeleting ? -60 : 0,
                  }}
                  exit={{ opacity: 0, scale: 0.8, x: -60 }}
                  transition={{
                    duration: 0.3,
                    delay: index * 0.02,
                    ease: [0.16, 1, 0.3, 1] as const,
                  }}
                  onClick={() => onSelect(session.id)}
                  onMouseEnter={() => setHoveredId(session.id)}
                  onMouseLeave={() => setHoveredId(null)}
                  className={`group flex items-center gap-2.5 px-3 py-2.5 rounded-xl cursor-pointer transition-all duration-200 relative ${
                    isActive
                      ? 'bg-accent/10 border border-accent/20 text-accent shadow-sm shadow-accent/5'
                      : 'hover:bg-bg-hover/80 text-text-secondary hover:text-text-primary border border-transparent'
                  }`}
                >
                  {/* Active indicator line */}
                  {isActive && (
                    <motion.div
                      layoutId="activeSession"
                      className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-6 rounded-full bg-accent"
                      transition={{ type: 'spring', stiffness: 500, damping: 30 }}
                    />
                  )}

                  <motion.div
                    animate={{
                      scale: isHovered ? 1.1 : 1,
                      rotate: isHovered ? 5 : 0,
                    }}
                    transition={{ type: 'spring', stiffness: 400, damping: 20 }}
                    className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 transition-colors duration-200 ${
                      isActive ? 'bg-accent/15' : 'bg-bg-tertiary/50 group-hover:bg-bg-tertiary'
                    }`}
                  >
                    <MessageSquare
                      size={13}
                      className={isActive ? 'text-accent' : 'text-text-muted group-hover:text-text-secondary'}
                    />
                  </motion.div>
                  <span className="text-sm truncate flex-1 font-medium">
                    {session.title}
                  </span>
                  <button
                    onClick={(e) => handleDelete(e, session.id)}
                    className={`flex-shrink-0 p-1.5 rounded-lg transition-all duration-200 ${
                      isActive
                        ? 'text-accent/30 hover:text-danger hover:bg-danger/10'
                        : 'opacity-0 group-hover:opacity-100 text-text-muted hover:text-danger hover:bg-danger/10'
                    }`}
                    title="Удалить"
                  >
                    <Trash2 size={13} />
                  </button>
                </motion.div>
              );
            })}
          </AnimatePresence>
        </div>

        {/* Footer */}
        <div className="p-3 border-t border-border/50">
          <div className="text-[11px] text-text-muted text-center flex items-center justify-center gap-1.5">
            <motion.div
              className="w-1.5 h-1.5 rounded-full bg-success"
              animate={{
                scale: [1, 1.3, 1],
                opacity: [0.5, 1, 0.5],
              }}
              transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
            />
            <span>Thule UI • {sessions.length} {pluralize(sessions.length)}</span>
          </div>
        </div>
      </aside>
    </>
  );
}
