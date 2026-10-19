import type {QueryResult, WidgetSpec} from "../../lib/api";
import {formatCell, unitLabel} from "../../lib/format";

export function KpiCard({widget, result}: {widget: WidgetSpec; result: QueryResult}) {
  const column = result.columns.find(item => item.name === widget.kpi);
  return <div className="kpiContent">
    <p className="kpiValue" title={String(result.rows[0]?.[widget.kpi] ?? "")}>{formatCell(result.rows[0]?.[widget.kpi], column?.type, column?.unit)}</p>
    <p className="widgetUnit">{unitLabel(column?.unit)}</p>
  </div>;
}
