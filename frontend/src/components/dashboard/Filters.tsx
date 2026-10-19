"use client";

import {FormEvent, useState} from "react";
import type {DashboardQueryRequest} from "../../lib/api";

export function Filters({busy, onApply}: {busy: boolean; onApply: (filters: DashboardQueryRequest) => void}) {
  const [error, setError] = useState("");
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const start = String(form.get("start") ?? "");
    const end = String(form.get("end") ?? "");
    if ((start && !end) || (!start && end) || (start && end && end < start)) {
      setError("Hãy nhập đủ ngày bắt đầu và kết thúc, theo đúng thứ tự.");
      return;
    }
    const filters: DashboardQueryRequest["filters"] = [];
    for (const field of ["state", "property_type"] as const) {
      const value = String(form.get(field) ?? "").trim();
      if (value) filters.push({field, op: "eq", value});
    }
    let date_range: DashboardQueryRequest["date_range"] = null;
    if (start && end) {
      const exclusive = new Date(end + "T00:00:00Z");
      exclusive.setUTCDate(exclusive.getUTCDate() + 1);
      date_range = {start, end_exclusive: exclusive.toISOString().slice(0, 10)};
    }
    setError("");
    onApply({filters, date_range});
  }
  return <form className="dashboardFilters" onSubmit={submit}>
    <label>Từ ngày<input type="date" name="start" min="0001-01-01" max="9998-12-31" /></label>
    <label>Đến ngày (bao gồm)<input type="date" name="end" min="0001-01-01" max="9998-12-31" /></label>
    <label>Bang<input name="state" placeholder="Ví dụ: CA" maxLength={40} /></label>
    <label>Loại bất động sản<input name="property_type" placeholder="Tên loại trong dữ liệu" maxLength={120} /></label>
    <button type="submit" disabled={busy}>{busy ? "Đang tải…" : "Áp dụng bộ lọc"}</button>
    {error ? <p role="alert" className="authError">{error}</p> : null}
  </form>;
}
