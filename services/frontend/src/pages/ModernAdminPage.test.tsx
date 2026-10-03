import { describe, expect, it } from "vitest";
import "@testing-library/jest-dom";
import { render, screen } from "@/test/utils";
import ModernAdminPage from "./ModernAdminPage";

describe("ModernAdminPage", () => {
  it("keeps the admin surface in the modern shell chrome", () => {
    render(
      <ModernAdminPage
        isAdminUser={false}
        isAdminValidated={false}
        username="admin"
      />,
    );

    expect(
      screen.getByRole("heading", {
        name: /manage users without the legacy shell/i,
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /back to platform/i }),
    ).toHaveAttribute("href", "/ax");
    expect(screen.queryByText("Concierge")).not.toBeInTheDocument();
  });
});
