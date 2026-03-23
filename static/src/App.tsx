import { useState, useEffect, useRef, useCallback, type ReactElement } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import type { Session, Message, Phase, ToolCall, ConfirmRequest, StreamingMessage } from './types';
import * as api from './api';
import Sidebar from './components/Sidebar';
import ChatHeader from './components/ChatHeader';
import ChatInput from './components/ChatInput';
import StatusBar from './components/StatusBar';
import ConfirmBar from './components/ConfirmBar';
import SettingsModal from './components/SettingsModal';
import EmptyState from './components/EmptyState';
import MessageBubble from './components/MessageBubble';

export function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentSession, setCurrentSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isBusy, setIsBusy] = useState(false);
  const [phase, setPhase] = useState<Phase>('idle');
  const [phaseStart, setPhaseStart] = useState<number | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [confirmRequest, setConfirmRequest] = useState<ConfirmRequest | null>(null);
  const [wsStatus, setWsStatus] = useState<'connected' | 'connecting' | 'reconnecting' | 'disconnected'>('connecting');
  const [allowAll, setAllowAll] = useState(false);
  const [provider, setProvider] = useState<'qwen' | 'claude'>('qwen');
  const [model, setModel] = useState<string>('sonnet');

  const [streaming, setStreaming] = useState<StreamingMessage>({
    thinking: '',
    content: '',
    tools: [],
  });
  const [isStreamingActive, setIsStreamingActive] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttempts = useRef(0);
  const currentSessionRef = useRef<Session | null>(null);

  useEffect(() => {
    currentSessionRef.current = currentSession;
  }, [currentSession]);

  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, streaming, scrollToBottom]);

  useEffect(() => {
    api.fetchSessions().then(setSessions).catch(console.error);
  }, []);

  // ─── WebSocket ───
  const disconnectWs = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
  }, []);

  const connectWs = useCallback(
    (sessionId: string) => {
      disconnectWs();
      // Используем 'reconnecting' если это не первая попытка
      setWsStatus(reconnectAttempts.current > 0 ? 'reconnecting' : 'connecting');
      const ws = api.createWebSocket(sessionId);
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttempts.current = 0;
        setPhase('idle');
        setWsStatus('connected');
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          handleWsMessage(data);
        } catch (e) {
          console.error('WS parse error:', e);
        }
      };

      ws.onclose = (event) => {
        // Не reconnect если:
        // 1. Сессия изменилась (пользователь переключился)
        // 2. Нормальное закрытие (1000, 1001, 1012 - reload сервера)
        // 3. Превышено количество попыток
        const isNormalClose = [1000, 1001, 1012, 1013].includes(event.code);
        const sessionChanged = currentSessionRef.current?.id !== sessionId;
        const maxAttemptsReached = reconnectAttempts.current >= 5;

        if (sessionChanged || isNormalClose || maxAttemptsReached) {
          // Окончательное отключение
          setWsStatus('disconnected');
          reconnectAttempts.current = 0;
          return;
        }

        // Начинаем переподключение — статус 'reconnecting'
        setWsStatus('reconnecting');

        // Reconnect только для той же сессии
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts.current), 10000);
        reconnectTimer.current = setTimeout(() => {
          reconnectAttempts.current++;
          if (currentSessionRef.current?.id === sessionId) {
            connectWs(sessionId);
          }
        }, delay);
      };

      ws.onerror = (e) => {
        // Не логируем ошибки слишком часто — reconnect справится
        if (reconnectAttempts.current === 0) {
          console.error('WS error:', e);
        }
      };
    },
    [disconnectWs]
  );

  // ─── WS Message Handler ───
  const handleWsMessage = useCallback((data: Record<string, unknown>) => {
    const type = data.type as string;

    switch (type) {
      case 'response_start':
        setIsBusy(true);
        setIsStreamingActive(true);
        setStreaming({ thinking: '', content: '', tools: [] });
        setPhase('waiting');
        setPhaseStart(Date.now());
        break;

      case 'stream_start':
        setPhase('generating');
        setPhaseStart(Date.now());
        break;

      case 'thinking':
        setPhase('thinking');
        setStreaming((prev) => ({
          ...prev,
          thinking: prev.thinking + (data.content as string),
        }));
        break;

      case 'content':
        setPhase('generating');
        setStreaming((prev) => ({
          ...prev,
          content: prev.content + (data.content as string),
        }));
        break;

      case 'tool_call':
        setPhase('tool');
        setStreaming((prev) => ({
          ...prev,
          tools: [
            ...prev.tools,
            {
              name: data.name as string,
              args: data.args as Record<string, unknown>,
            },
          ],
        }));
        break;

      case 'tool_result':
        setStreaming((prev) => {
          const tools = [...prev.tools];
          const idx = tools.findLastIndex(
            (t: { name: string; result?: string }) => t.name === (data.name as string) && !t.result
          );
          if (idx !== -1) {
            tools[idx] = { ...tools[idx], result: data.content as string };
          }
          return { ...prev, tools };
        });
        break;

      case 'tool_denied':
        setStreaming((prev) => {
          const tools = [...prev.tools];
          const idx = tools.findLastIndex(
            (t: { name: string; result?: string }) => t.name === (data.name as string) && !t.result
          );
          if (idx !== -1) {
            tools[idx] = {
              ...tools[idx],
              result: 'Запрещено пользователем',
              isDenied: true,
            };
          }
          return { ...prev, tools };
        });
        setConfirmRequest(null);
        break;

      case 'confirm_request':
        setConfirmRequest({
          name: data.name as string,
          args: data.args as Record<string, unknown>,
        });
        setPhase('confirming');
        break;

      case 'allow_all_enabled':
        setConfirmRequest(null);
        setAllowAll(true);
        break;

      case 'allow_all_changed':
        setAllowAll(data.value as boolean);
        break;

      case 'stream_end':
        break;

      case 'response_end':
        setIsStreamingActive(false);
        setIsBusy(false);
        setPhase('idle');
        setPhaseStart(null);
        setConfirmRequest(null);
        if (currentSessionRef.current) {
          api.fetchMessages(currentSessionRef.current.id).then(setMessages).catch(console.error);
        }
        setStreaming({ thinking: '', content: '', tools: [] });
        break;

      case 'stopped':
        setIsStreamingActive(false);
        setIsBusy(false);
        setPhase('idle');
        setPhaseStart(null);
        setConfirmRequest(null);
        setQuestionRequest(null);
        if (currentSessionRef.current) {
          api.fetchMessages(currentSessionRef.current.id).then((msgs) => {
            setMessages(msgs);
            setStreaming({ thinking: '', content: '', tools: [] });
          }).catch(console.error);
        } else {
          setStreaming({ thinking: '', content: '', tools: [] });
        }
        break;

      case 'error':
        setIsStreamingActive(false);
        setIsBusy(false);
        setPhase('idle');
        setPhaseStart(null);
        setConfirmRequest(null);
        setQuestionRequest(null);
        setStreaming({ thinking: '', content: '', tools: [] });
        break;

      case 'session_renamed': {
        const id = data.id as string;
        const title = data.title as string;
        setSessions((prev) => prev.map((s) => (s.id === id ? { ...s, title } : s)));
        setCurrentSession((prev) => (prev?.id === id ? { ...prev, title } : prev));
        break;
      }

      case 'ping':
        break;

      case 'background_task_completed':
        // Показываем уведомление о завершении фоновой задачи
        setStreaming((prev) => ({
          ...prev,
          content: prev.content + `\n\n✅ Фоновая задача завершена (${data.task_id}):\n${data.result}\n`,
        }));
        break;

      case 'background_task_failed':
        // Показываем уведомление об ошибке фоновой задачи
        setStreaming((prev) => ({
          ...prev,
          content: prev.content + `\n\n❌ Фоновая задача завершилась с ошибкой (${data.task_id}):\n${data.error}\n`,
        }));
        break;
    }
  }, []);

  // ─── Session actions ───
  const handleSelectSession = useCallback(
    async (id: string) => {
      if (currentSession?.id === id) return;
      const session = sessions.find((s) => s.id === id);
      if (!session) return;

      setCurrentSession(session);
      setMessages([]);
      setStreaming({ thinking: '', content: '', tools: [] });
      setIsStreamingActive(false);
      setIsBusy(false);
      setPhase('idle');
      setConfirmRequest(null);
      setProvider(session.provider || 'qwen');
      setModel(session.model || 'sonnet');

      try {
        const msgs = await api.fetchMessages(id);
        setMessages(msgs);
      } catch (e) {
        console.error('Failed to load messages:', e);
      }

      connectWs(id);
    },
    [currentSession, sessions, connectWs]
  );

  const handleCreateSession = useCallback(async () => {
    try {
      const session = await api.createSession('Новый чат', provider, model);
      setSessions((prev) => [session, ...prev]);
      setCurrentSession(session);
      setMessages([]);
      setStreaming({ thinking: '', content: '', tools: [] });
      setIsStreamingActive(false);
      setIsBusy(false);
      setPhase('idle');
      connectWs(session.id);
    } catch (e) {
      console.error('Failed to create session:', e);
    }
  }, [connectWs, provider, model]);

  const handleDeleteSession = useCallback(
    async (id: string) => {
      try {
        await api.deleteSession(id);
        setSessions((prev) => prev.filter((s) => s.id !== id));
        if (currentSession?.id === id) {
          setCurrentSession(null);
          setMessages([]);
          disconnectWs();
        }
      } catch (e) {
        console.error('Failed to delete session:', e);
      }
    },
    [currentSession, disconnectWs]
  );

  const handleSendMessage = useCallback(
    (text: string) => {
      if (!currentSession || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

      const userMsg: Message = {
        id: Date.now(),
        session_id: currentSession.id,
        role: 'user',
        content: text,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, userMsg]);
      wsRef.current.send(JSON.stringify({ type: 'message', content: text }));
    },
    [currentSession]
  );

  const handleStop = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'stop' }));
    }
  }, []);

  const handleConfirmAction = useCallback((action: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'confirm_response', action }));
    }
    setConfirmRequest(null);
    if (action !== 'deny') {
      setPhase('tool');
    }
  }, []);

  const handleToggleAllowAll = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'set_allow_all', value: !allowAll }));
    }
  }, [allowAll]);

  const handleRenamed = useCallback((id: string, title: string) => {
    setSessions((prev) => prev.map((s) => (s.id === id ? { ...s, title } : s)));
    setCurrentSession((prev) => (prev?.id === id ? { ...prev, title } : prev));
  }, []);

  const handleProviderChange = useCallback(async (newProvider: 'qwen' | 'claude') => {
    if (!currentSession) return;
    setProvider(newProvider);
    try {
      await api.saveSessionSettings(currentSession.id, {
        provider: newProvider,
        model: newProvider === 'claude' ? model : null,
      });
      // Update local session state
      setSessions((prev) => prev.map((s) =>
        s.id === currentSession.id ? { ...s, provider: newProvider, model: newProvider === 'claude' ? model : null } : s
      ));
      setCurrentSession((prev) => prev ? { ...prev, provider: newProvider, model: newProvider === 'claude' ? model : null } : null);
    } catch (e) {
      console.error('Failed to update provider:', e);
    }
  }, [currentSession, model]);

  const handleModelChange = useCallback(async (newModel: string) => {
    if (!currentSession) return;
    setModel(newModel);
    try {
      await api.saveSessionSettings(currentSession.id, {
        provider,
        model: newModel,
      });
      // Update local session state
      setSessions((prev) => prev.map((s) =>
        s.id === currentSession.id ? { ...s, model: newModel } : s
      ));
      setCurrentSession((prev) => prev ? { ...prev, model: newModel } : null);
    } catch (e) {
      console.error('Failed to update model:', e);
    }
  }, [currentSession, provider]);

  const renderedMessages = renderMessageList(messages);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isBusy) handleStop();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isBusy, handleStop]);

  return (
    <div className="h-screen w-screen flex bg-bg-primary text-text-primary overflow-hidden noise-overlay">
      <Sidebar
        sessions={sessions}
        currentSession={currentSession}
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onSelect={(id) => { handleSelectSession(id); setSidebarOpen(false); }}
        onCreate={() => { handleCreateSession(); setSidebarOpen(false); }}
        onDelete={handleDeleteSession}
      />

      <main className="flex-1 flex flex-col min-w-0 relative">
        {/* Ambient background glow */}
        <div className="absolute inset-0 pointer-events-none overflow-hidden">
          <div className="absolute -top-1/4 -right-1/4 w-1/2 h-1/2 bg-accent/[0.03] rounded-full blur-[100px]" />
          <div className="absolute -bottom-1/4 -left-1/4 w-1/2 h-1/2 bg-purple/[0.03] rounded-full blur-[100px]" />
        </div>

        <ChatHeader
          session={currentSession}
          onToggleSidebar={() => setSidebarOpen(true)}
          onOpenSettings={() => setSettingsOpen(true)}
          onExport={() => currentSession && api.exportSession(currentSession.id)}
          provider={provider}
          model={model}
          onProviderChange={handleProviderChange}
          onModelChange={handleModelChange}
        />

        <AnimatePresence mode="wait">
          {!currentSession ? (
            <motion.div
              key="no-session"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.3 }}
              className="flex-1"
            >
              <EmptyState hasSession={false} />
            </motion.div>
          ) : messages.length === 0 && !isStreamingActive ? (
            <motion.div
              key="empty-session"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              transition={{ duration: 0.3 }}
              className="flex-1"
            >
              <EmptyState hasSession={true} />
            </motion.div>
          ) : (
            <motion.div
              key="messages"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex-1 overflow-y-auto relative z-[1]"
            >
              <div className="max-w-4xl mx-auto px-4 md:px-6 py-6 space-y-6">
                {renderedMessages}

                {/* Streaming message */}
                <AnimatePresence>
                  {isStreamingActive && (
                    <motion.div
                      initial={{ opacity: 0, y: 12 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.3 }}
                      className="space-y-3"
                    >
                      {streaming.tools.length > 0 && (
                        <MessageBubble
                          role="assistant"
                          content=""
                          toolCalls={streaming.tools.map((t) => ({
                            function: { name: t.name, arguments: t.args },
                          }))}
                          toolResults={streaming.tools.map((t) => ({
                            content: t.result || '',
                            isDenied: t.isDenied,
                          }))}
                        />
                      )}

                      {(streaming.thinking || streaming.content || !streaming.tools.length) && (
                        <MessageBubble
                          role="assistant"
                          content={streaming.content}
                          thinking={streaming.thinking}
                          isStreaming={true}
                          isStreamingThinking={phase === 'thinking'}
                        />
                      )}
                    </motion.div>
                  )}
                </AnimatePresence>

                <div ref={messagesEndRef} />
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <AnimatePresence>
          {currentSession && (phase !== 'idle' || wsStatus !== 'connected') && (
            <StatusBar
              phase={phase}
              startTime={phaseStart}
              wsStatus={wsStatus}
              allowAll={allowAll}
              onToggleAllowAll={handleToggleAllowAll}
            />
          )}
        </AnimatePresence>

        <AnimatePresence>
          {confirmRequest && (
            <ConfirmBar
              name={confirmRequest.name}
              args={confirmRequest.args}
              onAction={handleConfirmAction}
            />
          )}
        </AnimatePresence>

        <ChatInput
          disabled={!currentSession}
          isBusy={isBusy}
          hasSession={!!currentSession}
          onSend={handleSendMessage}
          onStop={handleStop}
        />
      </main>

      <AnimatePresence>
        {currentSession && settingsOpen && (
          <SettingsModal
            session={currentSession}
            isOpen={settingsOpen}
            onClose={() => setSettingsOpen(false)}
            onRenamed={handleRenamed}
          />
        )}
      </AnimatePresence>
    </div>
  );
}

// ─── Message rendering helper ───
function renderMessageList(messages: Message[]): ReactElement[] {
  const elements: ReactElement[] = [];
  let i = 0;

  while (i < messages.length) {
    const msg = messages[i];

    if (msg.role === 'user') {
      elements.push(
        <motion.div
          key={msg.id}
          initial={{ opacity: 0, x: 30 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] as const }}
        >
          <MessageBubble role="user" content={msg.content} />
        </motion.div>
      );
      i++;
      continue;
    }

    if (msg.role === 'assistant' || msg.role === 'assistant_tool_call') {
      let toolCalls: ToolCall[] = [];
      if (msg.tool_calls) {
        try { toolCalls = JSON.parse(msg.tool_calls); } catch { /* */ }
      }

      const toolResults: Array<{ content: string; name?: string; isDenied?: boolean }> = [];

      if (toolCalls.length > 0) {
        i++;
        for (let tc = 0; tc < toolCalls.length && i < messages.length; tc++) {
          if (messages[i].role === 'tool') {
            const content = messages[i].content || '';
            toolResults.push({
              content,
              name: messages[i].tool_name || undefined,
              isDenied: content.startsWith('[ЗАПРЕЩЕНО]'),
            });
            i++;
          }
        }

        elements.push(
          <motion.div
            key={msg.id}
            initial={{ opacity: 0, x: -30 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] as const }}
          >
            <MessageBubble
              role="assistant"
              content={msg.content || ''}
              thinking={msg.thinking || undefined}
              toolCalls={toolCalls}
              toolResults={toolResults}
            />
          </motion.div>
        );

        if (i < messages.length && messages[i].role === 'assistant') {
          if (messages[i].content) {
            elements.push(
              <motion.div
                key={messages[i].id}
                initial={{ opacity: 0, x: -30 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] as const, delay: 0.1 }}
              >
                <MessageBubble
                  role="assistant"
                  content={messages[i].content}
                  thinking={messages[i].thinking || undefined}
                />
              </motion.div>
            );
          }
          i++;
        }
        continue;
      }

      elements.push(
        <motion.div
          key={msg.id}
          initial={{ opacity: 0, x: -30 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] as const }}
        >
          <MessageBubble
            role="assistant"
            content={msg.content || ''}
            thinking={msg.thinking || undefined}
          />
        </motion.div>
      );
      i++;
      continue;
    }

    if (msg.role === 'tool') {
      const isDenied = msg.content?.startsWith('[ЗАПРЕЩЕНО]');
      elements.push(
        <motion.div
          key={msg.id}
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          <MessageBubble
            role="assistant"
            content=""
            toolCalls={[{ function: { name: msg.tool_name || 'tool', arguments: {} } }]}
            toolResults={[{ content: msg.content || '', isDenied: !!isDenied }]}
          />
        </motion.div>
      );
      i++;
      continue;
    }

    i++;
  }

  return elements;
}

export default App;
