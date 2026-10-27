import {expect, test} from "@playwright/test";
import {DEMO_CURRENCY_NOTICE, formatCell, unitLabel} from "../../src/lib/format";

test("Demo currency labels disclose the USD assumption without converting values", () => {
  expect(unitLabel("dataset_currency")).toBe("USD (giả định trong demo)");
  expect(unitLabel("dataset_currency_per_transaction")).toBe("USD / giao dịch (giả định trong demo)");
  expect(DEMO_CURRENCY_NOTICE).toContain("Đơn vị tiền tệ trong demo được giả định là USD.");
  expect(DEMO_CURRENCY_NOTICE).toContain("không khai báo mã tiền tệ");
  expect(formatCell("1234.56", "decimal", "dataset_currency")).toBe("1.234,56");
  expect(unitLabel("transaction")).toBe("giao dịch");
  expect(formatCell("0.25", "decimal", "ratio")).toBe("25,00%");
});
