"use client";

import type {CSSProperties} from "react";
import type {DashboardDetail, DashboardQueryResponse, QueryResult} from "../../lib/api";
import {columnLabel, formatCell, unitLabel} from "../../lib/format";
import {KpiCard} from "./KpiCard";
import {ChartWidget} from "./ChartWidget";

function DataTable({result}: {result: QueryResult}) {
  return <div className="tableScroll"><table><thead><tr>{result.columns.map(column => <th key={column.name}>{columnLabel(column.name)}</th>)}</tr></thead>
    <tbody>{result.rows.map((row, index) => <tr key={index}>{result.columns.map(column =>
      <td key={column.name} title={String(row[column.name] ?? "")}>{formatCell(row[column.name], column.type, column.unit)}</td>)}</tr>)}</tbody></table></div>;
}

export function DashboardGrid({dashboard, data, loading}: {dashboard: Pick<DashboardDetail, "spec">; data: Pick<DashboardQueryResponse, "results"> | null; loading: boolean}) {
  return <div className="dashboardGrid" aria-busy={loading}>
    {dashboard.spec.widgets.map(widget => {
      const layout = dashboard.spec.layout.find(item => item.widget_id === widget.id);
      const result = data?.results[widget.query_id];
      const savedRange = dashboard.spec.queries.find(query => query.id === widget.query_id)?.plan.date_range;
      const style = {"--column": (layout?.x ?? 0) + 1, "--span": layout?.width ?? 4, "--row": (layout?.y ?? 0) + 1,
        "--height": layout?.height ?? 4} as CSSProperties;
      return <section key={widget.id} className={`dashboardWidget ${widget.chart === "card" ? "cardWidget" : ""}`} style={style} data-testid={`widget-${widget.id}`}>
        <h2>{widget.title}</h2>
        {savedRange?.start ? <p className="widgetUnit">Kỳ đã lưu: {savedRange.start} → trước {savedRange.end_exclusive}</p> : null}
        {loading || !result ? <p className="widgetMessage" role="status">Đang tải dữ liệu…</p>
          : result.status === "error" ? <p className="authError" role="alert">{result.warnings?.join(" ") || "Không thể tải widget."}</p>
          : result.status === "no_data" ? <p className="widgetMessage">Không có dữ liệu trong kỳ và phạm vi đã chọn.</p>
          : widget.chart === "card" ? <KpiCard widget={widget} result={result} />
          : <>
            <p className="widgetUnit">{unitLabel(result.columns.find(column => column.name === widget.kpi)?.unit)}</p>
            {widget.chart !== "table" ? <ChartWidget widget={widget} result={result} /> : null}
            {widget.chart === "table" ? <DataTable result={result} /> : <details><summary>Xem bảng dữ liệu</summary><DataTable result={result} /></details>}
          </>}
      </section>;
    })}
  </div>;
}
