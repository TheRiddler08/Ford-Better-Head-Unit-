import { describe, expect, it } from "vitest";
import { filterNavigationPages } from "../../../src/common/config/filter_navigation_pages";
import type { PageNavigation } from "../../../src/layouts/hass-tabs-subpage";
import type { HomeAssistant } from "../../../src/types";

describe("Car Assistant navigation", () => {
  it("blocks removed features even when marked core, retaining other admin rules", () => {
    const hass = {
      config: { components: ["cloud", "automation"] },
      user: { is_admin: true },
    } as unknown as HomeAssistant;
    const pages = [
      "/config/automation",
      "/config/script/edit/example",
      "/config/areas",
      "/config/cloud",
      "/config/voice-assistants",
      "/config/lovelace/dashboards",
      "/config/devices",
    ].map((path) => ({ path, core: true, adminOnly: true }) as PageNavigation);
    expect(filterNavigationPages(hass, pages).map((page) => page.path)).toEqual([
      "/config/lovelace/dashboards",
      "/config/devices",
    ]);
    hass.user!.is_admin = false;
    expect(filterNavigationPages(hass, pages)).toEqual([]);
  });
});
