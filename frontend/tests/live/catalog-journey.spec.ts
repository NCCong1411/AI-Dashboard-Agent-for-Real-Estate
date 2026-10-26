import {test} from "@playwright/test";
import {catalogJourney} from "../support/catalog-journey";

test("Live catalog creation, cross-fact edit and missing source refusal", async ({page}, info) => {
  await catalogJourney(page, info, "live");
});
