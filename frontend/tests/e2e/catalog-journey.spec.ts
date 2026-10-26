import {test} from "@playwright/test";
import {catalogJourney} from "../support/catalog-journey";

test("Catalog creation, offers addition, contextual chart edit and unsupported data", async ({page}, info) => {
  await catalogJourney(page, info, "mock");
});
