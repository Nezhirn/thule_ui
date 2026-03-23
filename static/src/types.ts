export interface Session {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  user_id?: string;
  system_prompt?: string;
  provider?: 'qwen' | 'claude';
  model?: string;
}

export interface Message {
  id: number;
  session_id: string;
  role: 'user' | 'assistant' | 'assistant_tool_call' | 'tool';
  content: string;
  thinking?: string;
  tool_calls?: string;
  tool_name?: string;
  created_at: string;
}

export interface ToolCall {
  id?: string;
  function: {
    name: string;
    arguments: Record<string, unknown>;
  };
}

export interface WsMessage {
  type: string;
  content?: string;
  name?: string;
  args?: Record<string, unknown>;
  id?: string;
  title?: string;
  action?: string;
}

export type Phase = 'idle' | 'waiting' | 'thinking' | 'generating' | 'tool' | 'confirming';

export interface ConfirmRequest {
  name: string;
  args: Record<string, unknown>;
}

export interface StreamingMessage {
  thinking: string;
  content: string;
  tools: Array<{
    name: string;
    args: Record<string, unknown>;
    result?: string;
    isDenied?: boolean;
  }>;
}
