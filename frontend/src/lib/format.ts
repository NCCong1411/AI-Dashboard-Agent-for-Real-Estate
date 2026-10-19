/** Round only for display, keeping decimal strings out of binary float math. */
export function formatDecimal(value: unknown, digits = 2): string {
  const text = String(value ?? "");
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(text);
  if (!match) return text || "—";
  const [, sign, whole, fraction = ""] = match;
  const scale = BigInt(10) ** BigInt(digits);
  let scaled = BigInt(whole) * scale + BigInt((fraction + "0".repeat(digits)).slice(0, digits) || "0");
  if (Number(fraction[digits] ?? "0") >= 5) scaled += BigInt(1);
  const integer = (scaled / scale).toLocaleString("vi-VN");
  const decimals = digits ? "," + (scaled % scale).toString().padStart(digits, "0") : "";
  return (sign && scaled !== BigInt(0) ? "−" : "") + integer + decimals;
}

export function formatCell(value: unknown, type?: string, unit?: string | null): string {
  if (value === null || value === undefined) return "—";
  if (unit === "ratio") {
    const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(String(value));
    if (!match) return String(value);
    const [, sign, whole, fraction = ""] = match;
    const digits = whole + fraction.padEnd(2, "0");
    const point = whole.length + 2;
    const scaled = sign + digits.slice(0, point) + (digits.length > point ? "." + digits.slice(point) : "");
    return formatDecimal(scaled) + "%";
  }
  if (type === "decimal") return formatDecimal(value);
  if (type === "integer") return formatDecimal(value, 0);
  return String(value);
}

export function unitLabel(unit?: string | null): string {
  if (unit === "dataset_currency") return "đơn vị tiền của dataset";
  if (unit === "dataset_currency_per_transaction") return "đơn vị tiền của dataset / giao dịch";
  if (unit === "transaction") return "giao dịch";
  if (unit === "property") return "bất động sản";
  if (unit === "offer") return "đề nghị";
  if (unit === "ratio") return "tỷ lệ (%)";
  return unit ?? "";
}

const columnLabels: Record<string, string> = {
  sales_value: "Giá trị giao dịch", transaction_count: "Số giao dịch",
  average_sale_value: "Giá trị giao dịch trung bình", sold_property_count: "Số BĐS có giao dịch",
  offer_count: "Số đề nghị", accepted_offer_rate: "Tỷ lệ đề nghị được chấp nhận", repair_cost: "Chi phí sửa chữa",
  day: "Ngày", month: "Tháng", quarter: "Quý", year: "Năm", state: "Bang", city: "Thành phố",
  property_type: "Loại bất động sản", agent: "Mã môi giới",
};

export function columnLabel(name: string): string {
  return columnLabels[name] ?? name;
}
