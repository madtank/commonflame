// Organization Classification Tests

interface TestOrganization {
  name: string;
  description?: string;
  visibility: string;
  member_count: number;
}

// Copy of the classification logic for testing
function getWorkspaceType(org: TestOrganization): string {
  // Personal workspace detection - check description pattern ONLY
  if (org.description?.startsWith("Personal workspace for")) {
    return "Personal";
  }

  // Public workspace
  if (org.visibility === "public") {
    return "Public";
  }

  // All other private workspaces are Team workspaces
  return "Team";
}

describe('Organization Classification', () => {
  test('aX Platform should be classified as Team', () => {
    const axPlatform = {
      name: "aX Platform",
      description: "Main collaboration platform for aX team",
      visibility: "private",
      member_count: 13
    };

    expect(getWorkspaceType(axPlatform)).toBe("Team");
  });

  test('Personal workspace with correct description', () => {
    const personal = {
      name: "John's Workspace",
      description: "Personal workspace for John Doe",
      visibility: "private",
      member_count: 1
    };

    expect(getWorkspaceType(personal)).toBe("Personal");
  });

  test('Single member private org without personal description is Team', () => {
    const singleTeam = {
      name: "Solo Project",
      description: "A project workspace",
      visibility: "private",
      member_count: 1
    };

    expect(getWorkspaceType(singleTeam)).toBe("Team");
  });

  test('Public workspace classification', () => {
    const publicOrg = {
      name: "Open Source",
      description: "Public collaboration space",
      visibility: "public",
      member_count: 50
    };

    expect(getWorkspaceType(publicOrg)).toBe("Public");
  });

  test('Multi-member private workspace is Team', () => {
    const team = {
      name: "Dev Team",
      description: "Development team workspace",
      visibility: "private",
      member_count: 5
    };

    expect(getWorkspaceType(team)).toBe("Team");
  });

  test('Empty description private workspace is Team', () => {
    const noDesc = {
      name: "Mystery Org",
      description: "",
      visibility: "private",
      member_count: 3
    };

    expect(getWorkspaceType(noDesc)).toBe("Team");
  });

  test('Case sensitive personal workspace detection', () => {
    const wrongCase = {
      name: "Test Workspace",
      description: "personal workspace for Someone", // lowercase 'p'
      visibility: "private",
      member_count: 1
    };

    expect(getWorkspaceType(wrongCase)).toBe("Team"); // Should be Team, not Personal
  });
});
