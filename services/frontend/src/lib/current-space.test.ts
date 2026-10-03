import { describe, expect, it } from "vitest";

import {
  classifySpaceForSwitcher,
  hasResolvedSpace,
  isHomeSpace,
  normalizeStoredSpaceId,
  selectDefaultSpace,
  selectLoginSpace,
  selectStoredOrCurrentSpace,
  getSpaceSwitcherLabel,
  getSpaceSwitcherTitle,
  resolveProvisionedHomeSpaceId,
} from "@/lib/current-space";

describe("current-space helpers", () => {
  it("detects personal spaces from the explicit flag", () => {
    expect(isHomeSpace({ is_personal: true })).toBe(true);
  });

  it("detects personal spaces from the JIT provisioning description", () => {
    expect(
      isHomeSpace({ description: "Personal workspace for Jane Doe" }),
    ).toBe(true);
  });

  it("prefers the current space over broad personal heuristics", () => {
    const selected = selectDefaultSpace([
      { id: "team", name: "Team", is_current: true },
      {
        id: "personal",
        name: "Jane's Workspace",
        description: "Personal workspace for Jane Doe",
      },
    ]);

    expect(selected?.id).toBe("team");
  });

  it("falls back to the current space when no personal space exists", () => {
    const selected = selectDefaultSpace([
      { id: "first", name: "First" },
      { id: "current", name: "Current", is_current: true },
    ]);

    expect(selected?.id).toBe("current");
  });

  it("falls back to the provisioned personal workspace when no current space exists", () => {
    const selected = selectDefaultSpace([
      { id: "draft", name: "Draft Smoke Personal", is_personal: true },
      {
        id: "home",
        name: "Jane's Workspace",
        description: "Personal workspace for Jane Doe",
      },
    ]);

    expect(selected?.id).toBe("home");
  });

  it("prefers the server current space over a stale stored space id", () => {
    const selected = selectStoredOrCurrentSpace(
      [
        { id: "draft", name: "Draft Smoke Personal", is_personal: true },
        { id: "home", name: "Jane's Workspace", is_current: true },
      ],
      "draft",
    );

    expect(selected?.id).toBe("home");
  });

  it("keeps login sticky to the backend current space instead of forcing home", () => {
    const selected = selectLoginSpace(
      [
        {
          id: "personal",
          name: "Jane's Workspace",
          is_personal: true,
        },
        { id: "team", name: "Team Hub", is_current: true },
      ],
      "personal",
    );

    expect(selected?.id).toBe("team");
  });

  it("reuses the last stored space on login when the backend has not marked one current", () => {
    const selected = selectLoginSpace(
      [
        { id: "team", name: "Team Hub" },
        { id: "last", name: "Last Space", slug: "last-space" },
        {
          id: "personal",
          name: "Jane's Workspace",
          is_personal: true,
        },
      ],
      "last",
    );

    expect(selected?.id).toBe("last");
  });

  it("matches the stored space by slug during login hydration", () => {
    const selected = selectLoginSpace(
      [
        { id: "team", name: "Team Hub" },
        { id: "last", name: "Last Space", slug: "last-space" },
      ],
      "last-space",
    );

    expect(selected?.id).toBe("last");
  });

  it("falls back to home on login only when neither backend nor storage specify a space", () => {
    const selected = selectLoginSpace([
      { id: "first", name: "First" },
      {
        id: "personal",
        name: "Jane's Workspace",
        is_personal: true,
      },
    ]);

    expect(selected?.id).toBe("personal");
  });

  it("drops placeholder space ids before they can be persisted or reused", () => {
    expect(normalizeStoredSpaceId("current-space")).toBeNull();
    expect(normalizeStoredSpaceId("default")).toBeNull();
    expect(normalizeStoredSpaceId("  ")).toBeNull();
    expect(normalizeStoredSpaceId("space-1")).toBe("space-1");
  });

  it("treats a space as resolved only when id and name are both present", () => {
    expect(hasResolvedSpace({ id: "space-1", name: "Jane's Workspace" })).toBe(
      true,
    );
    expect(hasResolvedSpace({ id: "space-1", name: "" })).toBe(false);
    expect(hasResolvedSpace({ id: "space-1" })).toBe(false);
    expect(hasResolvedSpace(null)).toBe(false);
  });

  it("only classifies the resolved provisioned home id as HOME", () => {
    const home = {
      id: "home-1",
      name: "Jane's Workspace",
      description: "Personal workspace for Jane Doe",
      is_personal: true,
    };
    const archive = {
      id: "archive1",
      name: "archive1",
      is_personal: true,
    };

    expect(classifySpaceForSwitcher(home, "home-1")).toBe("home");
    expect(classifySpaceForSwitcher(archive, "home-1")).toBe("private");
  });

  it("keeps personal or provisioned-looking spaces PRIVATE when the home id is missing or stale", () => {
    const provisionedLooking = {
      id: "maybe-home",
      name: "Jane's Workspace",
      description: "Personal workspace for Jane Doe",
      is_personal: true,
    };

    expect(classifySpaceForSwitcher(provisionedLooking, null)).toBe("private");
    expect(classifySpaceForSwitcher(provisionedLooking, "stale-home")).toBe(
      "private",
    );
  });

  it("classifies team and community spaces from metadata instead of display names", () => {
    expect(
      classifySpaceForSwitcher({
        id: "gateway-uat",
        name: "Gateway UAT Workspace",
        visibility: "private",
        is_personal: false,
        member_count: 3,
      }),
    ).toBe("team");
    expect(
      classifySpaceForSwitcher({
        id: "community",
        name: "New Space For Users Workspace",
        visibility: "subscribable",
        member_count: 1,
      }),
    ).toBe("community");
    expect(
      classifySpaceForSwitcher({
        id: "private-team-name",
        name: "UAT Team Lab",
        visibility: "private",
        is_personal: true,
        member_count: 1,
      }),
    ).toBe("private");
  });

  it("builds compact labels by stripping Workspace and truncating long names", () => {
    expect(
      getSpaceSwitcherLabel(
        {
          id: "gateway-uat",
          name: "Gateway UAT Workspace",
          visibility: "shared",
        },
        null,
      ),
    ).toBe("TEAM Gateway UAT");

    expect(
      getSpaceSwitcherLabel(
        {
          id: "long",
          name: "UAT Long Space Name For Dropdown Truncation Review Workspace",
          visibility: "community",
        },
        null,
        { maxNameLength: 24 },
      ),
    ).toBe("COMMUNITY UAT Long Space Name For...");
  });

  it("keeps full type, name, slug, and member count in the accessible title", () => {
    expect(
      getSpaceSwitcherTitle(
        {
          id: "gateway-uat",
          name: "Gateway UAT Workspace",
          slug: "gateway-uat",
          visibility: "shared",
          member_count: 4,
        },
        null,
      ),
    ).toBe("TEAM - Gateway UAT Workspace - @gateway-uat - 4 members");
  });

  it("resolves a provisioned home id without allowing name-only matches", () => {
    expect(
      resolveProvisionedHomeSpaceId([
        { id: "name-only", name: "Jane's Workspace" },
        {
          id: "home-1",
          name: "Jane's Workspace",
          description: "Personal workspace for Jane Doe",
          is_personal: true,
        },
      ]),
    ).toBe("home-1");
  });

  it("does not let an archived personal-space description steal the HOME label", () => {
    const archivedPrivate = {
      id: "archive1",
      name: "archive1",
      description: "Personal workspace for madtank",
      visibility: "private",
      is_personal: false,
      member_count: 1,
    };
    const actualHome = {
      id: "madtank-home",
      name: "madtank's Workspace",
      description: "Personal workspace for Jacob Taunton",
      visibility: "private",
      is_personal: true,
      member_count: 1,
    };

    expect(resolveProvisionedHomeSpaceId([archivedPrivate, actualHome])).toBe(
      "madtank-home",
    );
    expect(classifySpaceForSwitcher(archivedPrivate, "madtank-home")).toBe(
      "private",
    );
  });

  it("allows the resolved provisioned home to use a compact non-Workspace display name", () => {
    const home = {
      id: "codex-uat-home",
      name: "codex_uat",
      description: "Personal workspace for Codex UAT",
      is_personal: true,
    };

    expect(resolveProvisionedHomeSpaceId([home])).toBe("codex-uat-home");
    expect(classifySpaceForSwitcher(home, "codex-uat-home")).toBe("home");
  });
});
