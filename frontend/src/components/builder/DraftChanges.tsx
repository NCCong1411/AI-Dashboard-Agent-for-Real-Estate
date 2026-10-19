import type {components} from "../../types/api.generated";

export function DraftChanges({changes}: {changes: components["schemas"]["DraftChanges"] | null | undefined}) {
  if (!changes) return null;
  const labels = {added: "Thêm", removed: "Bỏ", updated: "Cập nhật"};
  return <section className="draftChanges" aria-label="Thay đổi của bản nháp">
    <h3>{changes.presentation_only ? "Thay đổi cách trình bày" : "Thay đổi của bản nháp"}</h3>
    <ul>
      {changes.title_changed ? <li>Đổi tên dashboard.</li> : null}
      {changes.widgets?.map(widget => <li key={widget.widget_id}>{labels[widget.change]}: {widget.title}</li>)}
      {!changes.widgets?.length && changes.changed_queries?.length ? <li>Cập nhật bộ lọc hoặc kỳ phân tích.</li> : null}
      {!changes.widgets?.length && !changes.changed_queries?.length && !changes.title_changed ? <li>Không có thay đổi mới.</li> : null}
    </ul>
  </section>;
}
