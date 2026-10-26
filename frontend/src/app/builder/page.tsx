"use client";

import Link from "next/link";
import {useRouter, useSearchParams} from "next/navigation";
import {Suspense, useEffect, useRef, useState} from "react";
import {AgentResponse, ApiError, ApprovalRequest, type AuthUser, ConversationView, createConversation, currentUser, decideDraft, getConversation, sendMessage} from "../../lib/api";
import {ChatPanel} from "../../components/builder/ChatPanel";
import {PreviewPanel} from "../../components/builder/PreviewPanel";
import {ApprovalPanel} from "../../components/builder/ApprovalPanel";
import {WorkspaceShell} from "../../components/ui/WorkspaceShell";
import {Icon} from "../../components/ui/Icon";

function Builder() {
  const router = useRouter();
  const params = useSearchParams();
  const conversationId = params.get("conversation");
  const dashboardId = params.get("dashboard");
  const [conversation, setConversation] = useState<ConversationView | null>(null);
  const [identity, setIdentity] = useState<AuthUser | null>(null);
  const [response, setResponse] = useState<AgentResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const loadedConversation = useRef<string | null>(null);
  useEffect(() => {
    if (conversationId && loadedConversation.current === conversationId) return;
    let active = true;
    const controller = new AbortController();
    async function load() {
      try {
        const user = await currentUser();
        if (!active) return;
        if (!user) {router.replace("/login"); return;}
        if (user.role !== "builder") {router.replace("/dashboards"); return;}
        setIdentity(user);
        const current = conversationId ? await getConversation(conversationId, controller.signal)
          : await createConversation(dashboardId ?? undefined, controller.signal);
        if (!active) return;
        loadedConversation.current = current.id;
        setConversation(current); setResponse(current.response ?? null); setBusy(current.status === "running");
        if (!conversationId) router.replace(`/builder?conversation=${encodeURIComponent(current.id)}`);
      } catch (reason) {
        if (!active) return;
        if (reason instanceof ApiError && (reason.status === 401 || reason.status === 403)) {
          setConversation(null); setResponse(null); router.replace(reason.status === 401 ? "/login" : "/dashboards");
        } else setError(reason instanceof Error ? reason.message : "Không thể mở hội thoại.");
      } finally {if (active) setLoading(false);}
    }
    void load();
    return () => {active = false; controller.abort();};
  }, [conversationId, dashboardId, router]);

  useEffect(() => {
    if (conversation?.status !== "running") return;
    const controller = new AbortController();
    const timer = setInterval(() => {
      getConversation(conversation.id, controller.signal).then(current => {
        setConversation(current); setResponse(current.response ?? null); setBusy(current.status === "running");
      }).catch(reason => {
        if (controller.signal.aborted) return;
        if (reason instanceof ApiError && (reason.status === 401 || reason.status === 403)) {
          setConversation(null); setResponse(null); router.replace(reason.status === 401 ? "/login" : "/dashboards");
        } else setError("Không thể tải trạng thái hội thoại. Hãy tải lại trang.");
      });
    }, 2000);
    return () => {clearInterval(timer); controller.abort();};
  }, [conversation?.id, conversation?.status, router]);

  async function submit(message: string): Promise<boolean> {
    if (!conversation || busy) return false;
    setBusy(true); setError("");
    try {
      const result = await sendMessage(conversation.id, message, conversation.draft_revision);
      setResponse(result);
      const current = await getConversation(conversation.id);
      setConversation(current); setResponse(current.response ?? result);
      return true;
    } catch (reason) {
      if (reason instanceof ApiError && (reason.status === 401 || reason.status === 403)) {
        setConversation(null); setResponse(null); router.replace(reason.status === 401 ? "/login" : "/dashboards");
      } else {
        setError(reason instanceof ApiError && reason.status === 409 ? "Hội thoại đang chạy hoặc bản nháp đã thay đổi. Hãy tải lại để xem trạng thái mới nhất."
          : reason instanceof Error ? reason.message : "Không thể gửi yêu cầu.");
      }
      return false;
    } finally {setBusy(false);}
  }

  async function decide(decision: ApprovalRequest["decision"]): Promise<void> {
    if (!conversation || !conversation.draft_hash || busy) return;
    setBusy(true); setError("");
    try {
      await decideDraft(conversation.id, {decision, draft_revision: conversation.draft_revision,
        draft_hash: conversation.draft_hash, base_version_number: conversation.base_version_number});
      const current = await getConversation(conversation.id);
      setConversation(current); setResponse(current.response ?? null);
    } catch (reason) {
      if (reason instanceof ApiError && (reason.status === 401 || reason.status === 403)) {
        setConversation(null); setResponse(null); router.replace(reason.status === 401 ? "/login" : "/dashboards");
      } else if (reason instanceof ApiError && reason.status === 409) {
        setError(reason.code === "base_version_conflict"
          ? "Dashboard đã có phiên bản mới. Hãy mở bản đã duyệt và bắt đầu hội thoại chỉnh sửa mới."
          : "Bản nháp hoặc phiên bản đã thay đổi. Đã tải lại hội thoại; hãy kiểm tra trước khi duyệt.");
        try {
          const current = await getConversation(conversation.id);
          setConversation(current); setResponse(current.response ?? null);
        } catch { /* Keep the conflict visible if refresh is unavailable. */ }
      } else setError("Chưa nhận được kết quả duyệt. Bạn có thể thử lại; quyết định đã lưu sẽ không tạo phiên bản trùng.");
    } finally {setBusy(false);}
  }

  return <WorkspaceShell user={identity}><div className="builderShell">
    <Link href="/dashboards" className="backLink">← Dashboard đã duyệt</Link>
    <header className="dashboardHeader"><div><p className="eyebrow">Ý TƯỞNG CỦA BẠN, DASHBOARD CỦA BẠN</p><h1>Tạo dashboard bằng hội thoại</h1></div><span className="versionBadge"><Icon name="sparkles" size={16} /> AI Builder</span></header>
    {(response?.mode ?? conversation?.mode) === "mock" ? <p className="modeNotice">Chế độ thử nghiệm: yêu cầu được xử lý bằng mock; dữ liệu xem trước lấy từ database theo quyền của bạn.</p> : null}
    {error ? <p className="authError" role="alert">{error}</p> : null}
    {error && conversation?.dashboard_id ? <p><Link className="backLink" href={`/dashboards/${encodeURIComponent(conversation.dashboard_id)}`}>Mở dashboard đã duyệt hiện tại →</Link></p> : null}
    {loading ? <p role="status">Đang mở hội thoại…</p> : <div className="builderWorkspace">
      <ChatPanel messages={conversation?.messages ?? []} busy={busy} ready={conversation !== null && conversation.approval?.checkpoint_synced !== false} onSend={submit} />
      <div className="builderPreview">
        <ApprovalPanel conversation={conversation} busy={busy} onDecide={decide} />
        <PreviewPanel conversation={conversation} response={response} busy={busy} />
      </div>
    </div>}
  </div></WorkspaceShell>;
}

export default function BuilderPage() {
  return <Suspense fallback={<main><p>Đang mở trình tạo dashboard…</p></main>}><Builder /></Suspense>;
}
