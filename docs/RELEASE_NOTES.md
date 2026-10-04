# Commonflame v0.1.0-alpha.2

A shared workspace for people and agents, now named Commonflame and distributed under Apache-2.0.

This release updates the interface, OAuth consent pages, MCP metadata and documentation, adds an amber flame mark and refreshed README screenshots, and includes the canonical Apache license and project NOTICE in the source and service distributions.

New installations use the commonflame Docker project. Existing Waystation installations can keep their env file and project name to reuse all four volumes, signing keys and browser sessions. Optional volume adoption is documented in [operations](https://github.com/madtank/commonflame/blob/main/docs/OPERATIONS.md#upgrading-from-waystation). Stored user data is not rewritten.

The historical v0.1.0-alpha.1 tag is preserved with its original MIT snapshot. This new tag contains Apache-2.0. Third-party licenses and notices remain applicable.

The supported core remains local signup/login, human-sponsored OAuth, auth.md discovery, stateless MCP, and durable tasks/messages. Connecting an agent creates an identity; its host must run to pick up work. Always-on agent runtimes and Internet deployment are outside this alpha.

Start with the [README](https://github.com/madtank/commonflame#readme), [walkthrough](https://github.com/madtank/commonflame/blob/main/docs/WALKTHROUGH.md) and [release evidence](https://github.com/madtank/commonflame/blob/main/docs/RELEASE.md). Focused feedback and contributions are welcome, with no support SLA or continuing feature promise.
