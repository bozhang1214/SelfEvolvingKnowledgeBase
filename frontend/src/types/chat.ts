export interface Conversation {
  conv_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  user_id: string;
  pinned?: boolean;
}

export interface Message {
  message_id: string;
  conv_id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  metadata?: {
    intent?: string;
    model?: string;
    token_usage?: {
      prompt_tokens: number;
      completion_tokens: number;
      total_tokens: number;
    };
    latency_ms?: number;
  };
}

export interface ChatRequest {
  conv_id: string;
  message: string;
}

export interface ChatMeta {
  conversation_id: string;
  title?: string;
  intent: string;
  intent_confidence: number;
  metrics: Record<string, unknown>;
  trace_id: string;
  latency_ms: number;
  ingest_status: string;
  ingest_reason: string;
}

export interface StreamChunk {
  type: 'token' | 'done' | 'error';
  content?: string;
  meta?: ChatMeta;
  error?: string;
}