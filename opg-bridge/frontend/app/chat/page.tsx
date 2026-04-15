"use client";

import { useCallback, useRef, useState } from "react";
import Link from "next/link";

const MODELS = [
  { id: "openai/gpt-4o", label: "GPT-4o" },
  { id: "openai/o4-mini", label: "o4-mini" },
  { id: "anthropic/claude-3.7-sonnet", label: "Claude 3.7 Sonnet" },
  { id: "anthropic/claude-3.5-haiku", label: "Claude 3.5 Haiku" },
  { id: "google/gemini-2.5-flash", label: "Gemini 2.5 Flash" },
  { id: "google/gemini-2.5-pro", label: "Gemini 2.5 Pro" },
  { id: "x-ai/grok-3-beta", label: "Grok 3 Beta" },
];

type Message = { role: "user" | "assistant" | "system"; content: string };
type PaymentReceipt = { txHash?: string; network?: string } | null;

export default function ChatPage() {
  const [model, setModel] = useState(MODELS[0].id);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [lastReceipt, setLastReceipt] = useState<PaymentReceipt>(null);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = useCallback(() => {
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  }, []);

  async function handleSend() {
    const text = input.trim();
    if (!text || loading) return;

    const userMsg: Message = { role: "user", content: text };
    const newMessages = [...messages, userMsg];
    setMessages(newMessages);
    setInput("");
    setLoading(true);
    setError(null);
    setLastReceipt(null);
    scrollToBottom();

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model,
          messages: newMessages,
          max_tokens: 800,
          temperature: 0.7,
          settlement: "batch",
        }),
      });

      const data = await res.json();
      if (!res.ok) {
        setError(data.error || `Error ${res.status}`);
        return;
      }

      const assistantContent =
        data.choices?.[0]?.message?.content ?? "(no response)";
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: assistantContent },
      ]);

      if (data._paymentReceipt) {
        setLastReceipt(data._paymentReceipt);
      }
    } catch (err: any) {
      setError(err.message || "Network error");
    } finally {
      setLoading(false);
      scrollToBottom();
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col px-6 py-8">
      {/* Header */}
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">x402 LLM Chat</h1>
          <p className="text-xs text-slate-400">
            Verified inference via OpenGradient TEE — paid with $OPG
          </p>
        </div>
        <Link
          href="/"
          className="rounded-lg bg-slate-800 px-3 py-2 text-sm text-slate-300 hover:bg-slate-700"
        >
          Bridge
        </Link>
      </header>

      {/* Model selector */}
      <div className="mb-4 flex items-center gap-3">
        <label className="text-xs uppercase tracking-wider text-slate-400">
          Model
        </label>
        <select
          value={model}
          onChange={(e) => setModel(e.target.value)}
          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-200 outline-none focus:border-indigo-500"
        >
          {MODELS.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => {
            setMessages([]);
            setError(null);
            setLastReceipt(null);
          }}
          className="ml-auto text-xs text-slate-500 hover:text-slate-300"
        >
          Clear chat
        </button>
      </div>

      {/* Messages */}
      <div className="mb-4 flex-1 space-y-3 overflow-y-auto rounded-xl border border-slate-800 bg-slate-950/50 p-4"
           style={{ minHeight: 300, maxHeight: "60vh" }}>
        {messages.length === 0 && (
          <p className="text-center text-sm text-slate-500">
            Send a message to start. Payment handled server-side with $OPG.
          </p>
        )}
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[85%] rounded-xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap ${
                msg.role === "user"
                  ? "bg-indigo-500/20 text-indigo-100"
                  : "bg-slate-800 text-slate-200"
              }`}
            >
              {msg.content}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="rounded-xl bg-slate-800 px-4 py-2.5 text-sm text-slate-400">
              Thinking...
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Error */}
      {error && (
        <div className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Payment receipt */}
      {lastReceipt?.txHash && (
        <div className="mb-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-2 text-xs text-emerald-300">
          Payment settled: {lastReceipt.txHash.slice(0, 14)}…{" "}
          <a
            href={`https://sepolia.basescan.org/tx/${lastReceipt.txHash}`}
            target="_blank"
            rel="noreferrer"
            className="underline hover:text-emerald-200"
          >
            view on BaseScan
          </a>
        </div>
      )}

      {/* Input */}
      <div className="flex gap-2">
        <textarea
          rows={1}
          placeholder="Type a message… (Enter to send, Shift+Enter for newline)"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          className="flex-1 resize-none rounded-xl border border-slate-700 bg-slate-900 px-4 py-3 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-indigo-500"
        />
        <button
          type="button"
          disabled={loading || !input.trim()}
          onClick={handleSend}
          className="rounded-xl bg-emerald-500 px-5 py-3 text-sm font-semibold text-white hover:bg-emerald-400 disabled:opacity-50"
        >
          Send
        </button>
      </div>

      <footer className="mt-6 text-center text-xs text-slate-500">
        Powered by OpenGradient x402 · TEE verified · $OPG on Base Sepolia
      </footer>
    </main>
  );
}
