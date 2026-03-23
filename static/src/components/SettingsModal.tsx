import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, RotateCcw, Save, Info } from 'lucide-react';
import type { Session } from '../types';
import {
  fetchSessionPrompt,
  saveSessionPrompt,
  renameSession as apiRename,
  fetchDefaultPrompt,
  saveSessionSettings,
} from '../api';

interface Props {
  session: Session;
  isOpen: boolean;
  onClose: () => void;
  onRenamed: (id: string, title: string) => void;
}

export default function SettingsModal({
  session,
  isOpen,
  onClose,
  onRenamed,
}: Props) {
  const [title, setTitle] = useState('');
  const [prompt, setPrompt] = useState('');
  const [defaultPrompt, setDefaultPrompt] = useState('');
  const [provider, setProvider] = useState<'qwen' | 'claude'>('qwen');
  const [model, setModel] = useState<string>('sonnet');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (isOpen && session) {
      setTitle(session.title);
      setProvider(session.provider || 'qwen');
      setModel(session.model || 'sonnet');
      fetchSessionPrompt(session.id).then(setPrompt);
      fetchDefaultPrompt().then(setDefaultPrompt);
    }
  }, [isOpen, session]);

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    if (isOpen) {
      document.addEventListener('keydown', handleEsc);
      document.body.style.overflow = 'hidden';
    }
    return () => {
      document.removeEventListener('keydown', handleEsc);
      document.body.style.overflow = '';
    };
  }, [isOpen, onClose]);

  const handleSave = async () => {
    setSaving(true);
    try {
      if (title !== session.title) {
        await apiRename(session.id, title);
        onRenamed(session.id, title);
      }
      await saveSessionPrompt(session.id, prompt || null);

      // Save provider and model
      await saveSessionSettings(session.id, {
        provider,
        model: provider === 'claude' ? model : null,
      });

      onClose();
    } catch (e) {
      console.error('Save error:', e);
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
          className="fixed inset-0 z-[100] flex items-center justify-center p-4"
          onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
        >
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 bg-black/70 backdrop-blur-md"
          />

          {/* Modal */}
          <motion.div
            initial={{ opacity: 0, scale: 0.9, y: 30 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.9, y: 30 }}
            transition={{ type: 'spring', stiffness: 350, damping: 30 }}
            className="relative w-full max-w-lg glass rounded-2xl border border-border/50 shadow-2xl shadow-black/50"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between p-5 border-b border-border/50">
              <h2 className="text-base font-bold text-text-primary">
                Настройки сессии
              </h2>
              <button
                onClick={onClose}
                className="p-1.5 rounded-lg hover:bg-bg-hover text-text-secondary transition-all duration-200 hover:text-text-primary hover:rotate-90"
              >
                <X size={18} />
              </button>
            </div>

            {/* Body */}
            <div className="p-5 space-y-5">
              <div>
                <label className="block text-xs font-semibold text-text-secondary mb-2 uppercase tracking-wider">
                  Название
                </label>
                <input
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  className="w-full py-2.5 px-4 text-sm rounded-xl bg-bg-primary/80 border border-border/60 text-text-primary placeholder:text-text-muted outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent/10 transition-all duration-200"
                  placeholder="Введите название..."
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-text-secondary mb-2 uppercase tracking-wider">
                  Системный промпт
                </label>
                <textarea
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  rows={6}
                  className="w-full py-3 px-4 text-sm rounded-xl bg-bg-primary/80 border border-border/60 text-text-primary placeholder:text-text-muted outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent/10 transition-all duration-200 resize-none font-mono"
                  placeholder="Введите системный промпт..."
                />
                <p className="mt-1.5 text-[11px] text-text-muted">
                  Оставьте пустым для промпта по умолчанию
                </p>
              </div>

              <div>
                <label className="block text-xs font-semibold text-text-secondary mb-2 uppercase tracking-wider">
                  Провайдер
                </label>
                <select
                  value={provider}
                  onChange={(e) => setProvider(e.target.value as 'qwen' | 'claude')}
                  className="w-full py-2.5 px-4 text-sm rounded-xl bg-bg-primary/80 border border-border/60 text-text-primary outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent/10 transition-all duration-200"
                >
                  <option value="qwen">Qwen</option>
                  <option value="claude">Claude</option>
                </select>
              </div>

              {provider === 'claude' && (
                <div>
                  <label className="block text-xs font-semibold text-text-secondary mb-2 uppercase tracking-wider">
                    Модель Claude
                  </label>
                  <select
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    className="w-full py-2.5 px-4 text-sm rounded-xl bg-bg-primary/80 border border-border/60 text-text-primary outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent/10 transition-all duration-200"
                  >
                    <option value="opus">Opus (наиболее способная)</option>
                    <option value="sonnet">Sonnet (сбалансированная)</option>
                    <option value="haiku">Haiku (быстрая)</option>
                  </select>
                </div>
              )}

              <div className="flex items-start gap-2.5 p-3.5 rounded-xl bg-accent/[0.05] border border-accent/10">
                <Info size={14} className="text-accent/60 flex-shrink-0 mt-0.5" />
                <p className="text-xs text-text-secondary leading-relaxed">
                  Системный промпт определяет поведение и инструкции для модели в рамках этой сессии.
                </p>
              </div>
            </div>

            {/* Footer */}
            <div className="flex items-center justify-between p-5 border-t border-border/50">
              <button
                onClick={() => setPrompt(defaultPrompt)}
                className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-text-secondary hover:text-text-primary rounded-lg hover:bg-bg-hover transition-all duration-200"
              >
                <RotateCcw size={13} />
                Сбросить
              </button>
              <div className="flex gap-2.5">
                <button
                  onClick={onClose}
                  className="px-4 py-2 text-sm font-medium text-text-secondary hover:text-text-primary rounded-xl border border-border/60 hover:bg-bg-hover transition-all duration-200"
                >
                  Отмена
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="flex items-center gap-1.5 px-5 py-2 text-sm font-semibold text-white bg-gradient-to-r from-accent to-accent-dark hover:from-accent-dark hover:to-accent rounded-xl transition-all duration-300 hover:shadow-lg hover:shadow-accent/25 disabled:opacity-50 active:scale-[0.97]"
                >
                  <Save size={14} />
                  {saving ? 'Сохраняю...' : 'Сохранить'}
                </button>
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
