"use client";

import {FormEvent, useEffect, useRef, useState} from "react";
import type {ConversationView} from "../../lib/api";

export function ChatPanel({messages, busy, ready, onSend}: {
  messages: ConversationView["messages"]; busy: boolean; ready: boolean; onSend: (message: string) => Promise<boolean>;
}) {
  const [message, setMessage] = useState("");
  const messagesElement = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = messagesElement.current;
    if (element) element.scrollTo({top: element.scrollHeight});
  }, [messages.length, busy]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !ready || !message.trim()) return;
    if (await onSend(message.trim())) setMessage("");
  }
  return <section className="chatPanel" aria-label="Hội thoại tạo dashboard">
    <h2>Yêu cầu phân tích</h2>
    <div ref={messagesElement} className="chatMessages" aria-live="polite" aria-relevant="additions text">
      {messages.length === 0 ? <p className="dashboardHint">Thử: “Tạo tổng quan giao dịch năm 2025 theo tháng, bang và loại bất động sản.”</p> : null}
      {messages.map((item, index) => <article className={`chatMessage ${item.role}`} key={`${item.created_at}-${index}`}>
        <p className="messageRole">{item.role === "user" ? "Bạn" : "Trợ lý"}</p><p>{item.content}</p>
      </article>)}
      {busy ? <p role="status">Đang xử lý yêu cầu…</p> : null}
    </div>
    <form onSubmit={submit} className="chatForm">
      <label htmlFor="builder-message">Nội dung yêu cầu</label>
      <textarea id="builder-message" value={message} onChange={event => setMessage(event.target.value)}
        maxLength={12000} rows={5} disabled={!ready || busy} placeholder="KPI, kỳ thời gian và chiều phân tích bạn cần…" />
      <button type="submit" disabled={!ready || busy || !message.trim()}>{busy ? "Đang xử lý…" : "Gửi yêu cầu"}</button>
    </form>
  </section>;
}
