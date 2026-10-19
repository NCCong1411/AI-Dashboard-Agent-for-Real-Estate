"use client";

import {useEffect, useRef, useState} from "react";
import * as echarts from "echarts/core";
import {BarChart, LineChart, PieChart} from "echarts/charts";
import {AriaComponent, GridComponent, LegendComponent, TooltipComponent} from "echarts/components";
import {CanvasRenderer} from "echarts/renderers";
import type {QueryResult, WidgetSpec} from "../../lib/api";

echarts.use([BarChart, LineChart, PieChart, AriaComponent, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

export function ChartWidget({widget, result}: {widget: WidgetSpec; result: QueryResult}) {
  const element = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!element.current) return;
    const chart = echarts.init(element.current);
    const labels = result.rows.map(row => String(row[widget.dimension ?? ""] ?? "—"));
    const ratio = result.columns.find(column => column.name === widget.kpi)?.unit === "ratio";
    const values = result.rows.map(row => row[widget.kpi] == null ? null : Number(row[widget.kpi]) * (ratio ? 100 : 1));
    const axisValue = (value: number) => value.toLocaleString("vi-VN", {notation: "compact", maximumFractionDigits: ratio ? 2 : 1}) + (ratio ? "%" : "");
    const pie = widget.chart === "pie" || widget.chart === "donut";
    const horizontal = widget.chart === "horizontal_bar";
    try {
      chart.setOption({
        animation: false,
        color: ["#1f7650", "#84aa8b", "#d3a353", "#647d99", "#b86d4d", "#8e7c9c", "#718b72"],
        aria: {enabled: true},
        tooltip: {trigger: pie ? "item" : "axis", renderMode: "richText", confine: true,
          ...(ratio ? {valueFormatter: (value: unknown) => Number(value).toLocaleString("vi-VN", {maximumFractionDigits: 2}) + "%"} : {})},
        ...(pie ? {legend: {bottom: 0, type: "scroll"}} : {
          grid: {left: 8, right: 16, top: 20, bottom: 32, containLabel: true},
          xAxis: horizontal ? {type: "value", splitNumber: 3, axisLabel: {hideOverlap: true, formatter: axisValue}} : {type: "category", data: labels, axisLabel: {hideOverlap: true}},
          yAxis: horizontal ? {type: "category", data: labels, axisLabel: {width: 95, overflow: "truncate"}} : {type: "value", axisLabel: {hideOverlap: true, formatter: axisValue}},
        }),
        series: [pie ? {
          name: widget.title, type: "pie", radius: widget.chart === "donut" ? ["40%", "65%"] : "65%",
          center: ["50%", "43%"], label: {show: false},
          data: labels.map((name, index) => ({name, value: values[index]})),
        } : {
          name: widget.title, type: widget.chart === "line" ? "line" : "bar", data: values,
          smooth: false, connectNulls: false, showSymbol: false,
        }],
      });
    } catch {
      // Keep the exact data table available if the chart cannot be drawn.
      queueMicrotask(() => setFailed(true));
    }
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(element.current);
    return () => {observer.disconnect(); chart.dispose();};
  }, [result, widget]);
  return <>
    {failed ? <p role="status">Không thể vẽ biểu đồ. Bạn có thể xem bảng dữ liệu bên dưới.</p> : null}
    <div ref={element} className="chartCanvas" role="img" aria-label={widget.title} />
  </>;
}
