"""Static regression tests for MCP widget callServerTool compatibility."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


WIDGET_DIR = Path(__file__).resolve().parents[1] / "fastmcp_server" / "resources" / "static" / "widgets"
CONTEXT_TOOL = Path(__file__).resolve().parents[1] / "fastmcp_server" / "tools" / "context.py"


class WidgetCallContractTests(unittest.TestCase):
    """Guard the widget fallback tool-call shape used by non-OpenAI hosts."""

    def test_widget_fallbacks_use_object_form_call_server_tool(self):
        expected_fragment = "return app.callServerTool({ name, arguments: args });"
        forbidden_fragment = "return app.callServerTool(name, args);"
        widget_files = sorted(WIDGET_DIR.glob("*.html"))

        missing = []
        positional = []
        for path in widget_files:
            content = path.read_text(encoding="utf-8")
            if "async callServerTool(name, args)" not in content:
                continue
            if expected_fragment not in content:
                missing.append(path.name)
            if forbidden_fragment in content:
                positional.append(path.name)

        self.assertEqual(missing, [], f"Widgets missing object-form callServerTool fallback: {missing}")
        self.assertEqual(positional, [], f"Widgets still using positional callServerTool fallback: {positional}")

    def test_message_timeline_draft_send_has_loading_guard(self):
        content = (WIDGET_DIR / "message-timeline.html").read_text(encoding="utf-8")

        self.assertIn("draftSending: false", content)
        self.assertIn("draftContent: \"\"", content)
        self.assertIn('isSending ? "Sending..." : "Send"', content)
        self.assertIn("if (state.draftSending) return;", content)
        self.assertIn("sendBtn.disabled = isSending;", content)
        self.assertIn("discardBtn.disabled = isSending;", content)
        self.assertIn("const editor = document.createElement(\"textarea\");", content)
        self.assertIn("state.draftContent = data.draft.content || \"\";", content)

    def test_task_board_uses_canonical_cancelled_status(self):
        content = (WIDGET_DIR / "task-board.html").read_text(encoding="utf-8")

        self.assertIn('const DONE_STATUSES = ["completed", "cancelled", "canceled"];', content)
        self.assertIn('"completed", "cancelled"', content)
        self.assertIn('await updateTask(t.id, { status: "cancelled" });', content)
        self.assertIn('}, "Cancel task");', content)
        self.assertNotIn('status: "closed"', content)
        self.assertNotIn('"completed", "closed"', content)

    def test_task_board_uses_canonical_status_options(self):
        content = (WIDGET_DIR / "task-board.html").read_text(encoding="utf-8")

        self.assertIn("const STATUS_OPTIONS = [", content)
        self.assertIn('{ value: "not_started", label: "Open" }', content)
        self.assertIn('{ value: "blocked", label: "Blocked" }', content)
        self.assertIn("function canonicalStatusValue(value)", content)
        self.assertIn('status === "pending"', content)
        self.assertIn('return "not_started";', content)
        self.assertIn('canonicalStatusValue(task.status || "not_started")', content)
        self.assertIn('canonicalStatusValue(t.status || "not_started")', content)
        self.assertNotIn('["open", "pending", "in_progress", "completed", "cancelled"]', content)

    def test_task_board_surfaces_work_loop_fields(self):
        content = (WIDGET_DIR / "task-board.html").read_text(encoding="utf-8")

        self.assertIn("function ownerLabel(task)", content)
        self.assertIn("task?.owner || task?.creator", content)
        self.assertIn("function creatorLabel(task)", content)
        self.assertIn("function formatQueueLabel(value)", content)
        self.assertIn("reminder.next_fire_at || reminder.next_reminder_at", content)
        self.assertIn("reminder.last_fire_at || reminder.last_reminded_at || reminder.last_fired_at", content)
        self.assertIn("reminder.cadence_minutes", content)
        self.assertIn("reminder.fire_count", content)
        self.assertIn("task-work-pulse-owner", content)
        self.assertIn("task-work-pulse-assignee", content)
        self.assertIn("task-work-pulse-queue", content)
        self.assertIn("task-queue-label", content)
        self.assertIn("task-detail-owner", content)
        self.assertIn("task-detail-creator", content)
        self.assertIn("task-detail-queue", content)
        self.assertIn('callServerToolWithTimeout(\n          "spaces"', content)
        self.assertIn('{ action: "members" }', content)
        self.assertIn("function normalizeAssigneeOptions(result)", content)
        self.assertIn("assignee_type", content)
        self.assertIn("assignee_id", content)
        self.assertIn("Assign to a human owner or an agent in this space.", content)

    def test_task_board_assignment_change_uses_typed_contract_and_verifies_persistence(self):
        content = (WIDGET_DIR / "task-board.html").read_text(encoding="utf-8")

        self.assertIn("function taskMatchesAssigneeSelection(task, expectedAssignee)", content)
        self.assertIn("options.expectedAssignee", content)
        self.assertIn("Assignment did not update. The server returned the previous assignee", content)
        self.assertIn("{ assignee_type: null, assignee_id: null }", content)
        self.assertNotIn("{ assigned_agent_id: \"\" }", content)

    def test_task_board_suppresses_duplicate_detail_summary(self):
        content = (WIDGET_DIR / "task-board.html").read_text(encoding="utf-8")

        self.assertIn("function comparableSummaryText(text)", content)
        self.assertIn("function shouldShowDetailSummary(task, summary)", content)
        self.assertIn("const comparableSummary = comparableSummaryText(summary)", content)
        self.assertIn("const comparableDescription = comparableSummaryText(description)", content)
        self.assertIn("comparableSummary === comparableDescription", content)
        self.assertIn("shouldShowDetailSummary(t, detailSummary)", content)

    def test_task_board_detail_shows_work_pulse_before_description(self):
        content = (WIDGET_DIR / "task-board.html").read_text(encoding="utf-8")

        work_pulse_index = content.index("card.appendChild(renderWorkPulse(detail, t));")
        description_index = content.index('"data-testid": "task-detail-description"')

        self.assertLess(work_pulse_index, description_index)

    def test_task_board_detail_has_reminder_action_strip_and_supervisor_pulse(self):
        content = (WIDGET_DIR / "task-board.html").read_text(encoding="utf-8")

        self.assertIn("function renderReminderActionStrip(detail, task)", content)
        self.assertIn("task-reminder-action-strip", content)
        self.assertIn("task-reminder-strip-state", content)
        self.assertIn("task-supervisor-pulse-state", content)
        self.assertIn("task-reminder-strip-complete", content)
        self.assertIn("task-reminder-strip-snooze", content)
        self.assertIn("task-reminder-strip-cancel", content)
        self.assertIn("task-reminder-strip-schedule-until-done", content)
        self.assertIn('snoozed_until: snoozedUntil', content)
        self.assertIn('reminder_action: "snooze"', content)
        self.assertNotIn('reminder_action: "silence"', content)
        self.assertIn('reminder_action: "schedule"', content)
        self.assertIn('reminder_action: "cancel"', content)
        self.assertIn("function buildReminderScheduleArgs", content)
        self.assertIn("reminder_cadence_minutes", content)
        self.assertIn("reminder_max_count", content)
        self.assertIn("reminder_until_done", content)
        self.assertIn("next_fire_at", content)
        self.assertNotIn("Reminder lifecycle actions are not enabled by this server yet.", content)
        self.assertIn("function formatSupervisorPulseLabel(detail, task)", content)
        self.assertIn("task-work-pulse-supervisor", content)
        strip_index = content.index("card.appendChild(renderReminderActionStrip(detail, t));")
        pulse_index = content.index("card.appendChild(renderWorkPulse(detail, t));")
        description_index = content.index('"data-testid": "task-detail-description"')

        self.assertLess(strip_index, pulse_index)
        self.assertLess(strip_index, description_index)

    def test_task_board_reminder_until_done_checkbox_stays_inline(self):
        content = (WIDGET_DIR / "task-board.html").read_text(encoding="utf-8")

        self.assertIn('.form-field input[type="checkbox"]', content)
        self.assertIn("width: 16px;", content)
        self.assertIn(".checkbox-row", content)
        self.assertIn('const reminderUntilRow = el("div", { className: "checkbox-row" });', content)
        self.assertIn('const detailReminderUntilRow = el("div", { className: "checkbox-row" });', content)
        self.assertIn('for: "cf-reminder-until-done"', content)
        self.assertIn('for: "df-reminder-until-done"', content)

    def test_task_board_supervisor_pulse_check_shows_gateway_liveness_fields(self):
        content = (WIDGET_DIR / "task-board.html").read_text(encoding="utf-8")

        self.assertIn("function formatSupervisorWorkingLabel(detail, task)", content)
        self.assertIn("function formatSupervisorResultLabel(detail, task)", content)
        self.assertIn("function formatSupervisorBacklogLabel(detail, task)", content)
        self.assertIn("function formatMissingSignalsLabel(detail, task)", content)
        self.assertIn("task-work-pulse-last-received", content)
        self.assertIn("task-work-pulse-working", content)
        self.assertIn("task-work-pulse-last-result", content)
        self.assertIn("task-work-pulse-backlog", content)
        self.assertIn("task-work-pulse-stale", content)
        self.assertIn("task-work-pulse-missing-signals", content)
        self.assertIn("pulse.queue_depth", content)
        self.assertIn("pulse.missing_signals", content)

    def test_task_board_surfaces_copyable_reference_and_deep_link(self):
        content = (WIDGET_DIR / "task-board.html").read_text(encoding="utf-8")

        self.assertIn("function taskCopyableRef(task)", content)
        self.assertIn("task?.task_reference?.copyable_ref", content)
        self.assertIn("task?.task_ref", content)
        self.assertIn("function taskDeepLink(task)", content)
        self.assertIn("function isSafeTaskLink(value)", content)
        self.assertIn("if (structuredLink && isSafeTaskLink(structuredLink)) return String(structuredLink);", content)
        self.assertIn("if (flatLink && isSafeTaskLink(flatLink)) return String(flatLink);", content)
        self.assertIn("task?.task_reference?.deep_link", content)
        self.assertIn("task?.task_deep_link", content)
        self.assertIn("function copyTaskRef(task, button)", content)
        self.assertIn("navigator.clipboard.writeText", content)
        self.assertIn('const ok = document.execCommand("copy");', content)
        self.assertIn('if (!ok) throw new Error("copy command failed");', content)
        self.assertIn('if (displayId && displayId !== "task_legacy") return String(displayId);', content)
        self.assertIn("Backend emits task_legacy for unnumbered tasks", content)
        self.assertIn("if (copyableRef) {", content)
        self.assertIn('card.appendChild(copyRefBtn);', content)
        self.assertIn('target: isExternalTaskLink(detailLink) ? "_blank" : undefined', content)
        self.assertIn('rel: isExternalTaskLink(detailLink) ? "noopener noreferrer" : undefined', content)
        self.assertIn('data-testid": "task-copy-ref"', content)
        self.assertIn('card.addEventListener("keydown", (event) => {', content)
        self.assertIn('if (event.key === " ") event.preventDefault();', content)
        self.assertIn('if (event.key === "Enter") {', content)
        self.assertIn('card.addEventListener("keyup", (event) => {', content)
        self.assertIn('if (event.key !== " ") return;', content)
        # Regression guard: Space activation should stay split between keydown (prevent scroll)
        # and keyup (open detail) instead of reverting to the old keydown-only handler.
        self.assertNotIn('if (event.key !== "Enter" && event.key !== " ") return;', content)
        # Regression guard: the row-level keyboard handler must ignore the nested copy button.
        self.assertNotIn('copyRefBtn.addEventListener("keydown", (event) => {\n          event.stopPropagation();\n        });', content)
        self.assertIn('data-testid": "task-detail-copy-ref"', content)
        self.assertIn('data-testid": "task-detail-deep-link"', content)

    def test_agent_dashboard_preserves_hitl_draft_during_list_refresh(self):
        content = (WIDGET_DIR / "agent-dashboard.html").read_text(encoding="utf-8")

        self.assertIn("opened on a HITL draft", content)
        self.assertIn("if (state.draft) {", content)
        self.assertIn("render();\n        return;\n      }\n      state.draft = null;", content)

    def test_agent_dashboard_refreshes_persisted_draft_and_allows_mode_edit(self):
        content = (WIDGET_DIR / "agent-dashboard.html").read_text(encoding="utf-8")

        self.assertIn("async function refreshDraftStatus(draftId)", content)
        self.assertIn('action: "get_draft", draft_id: draftId', content)
        self.assertIn("changes.agent_mode = nextMode;", content)
        self.assertIn('["name", "description", "agent_mode"].includes(f.key)', content)

    def test_agent_dashboard_does_not_unconditionally_control_space_agents(self):
        content = (WIDGET_DIR / "agent-dashboard.html").read_text(encoding="utf-8")

        self.assertNotIn("if (isSpaceAgent(agent) && !isReadOnlyMode()) return true;", content)
        self.assertIn("if (isSpaceAgent(agent)) return false;", content)

    def test_agent_dashboard_current_space_filter_uses_placement_not_access(self):
        content = (WIDGET_DIR / "agent-dashboard.html").read_text(encoding="utf-8")

        self.assertIn("function explicitAgentPlacementSpaceIds(agent)", content)
        self.assertIn("function displaySpaceIdForAgent(agent)", content)
        self.assertIn("function agentPlacementSpaceIds(agent)", content)
        self.assertIn("const ids = agentPlacementSpaceIds(agent);", content)
        display_fn = content.split("function displaySpaceIdForAgent(agent)", 1)[1].split(
            "function agentSpaceBadges(agent)",
            1,
        )[0]
        self.assertIn("const explicitIds = explicitAgentPlacementSpaceIds(agent);", display_fn)
        self.assertIn("return explicitIds[0] || agent?.space_id || \"\";", display_fn)
        placement_fn = content.split("function agentPlacementSpaceIds(agent)", 1)[1].split(
            "function agentInCurrentSpace(agent)",
            1,
        )[0]
        self.assertIn("const explicitIds = explicitAgentPlacementSpaceIds(agent);", placement_fn)
        self.assertIn("if (explicitIds.length) return explicitIds;", placement_fn)
        self.assertIn("return uniqueSpaceIds([agent?.space_id]);", placement_fn)
        self.assertNotIn("space_access", placement_fn)

    def test_agent_dashboard_defers_cross_space_scope_counts_until_loaded(self):
        content = (WIDGET_DIR / "agent-dashboard.html").read_text(encoding="utf-8")

        self.assertIn("function isCrossSpaceScope(scope)", content)
        self.assertIn('return ["mine", "others", "all"].includes(scope);', content)
        self.assertIn("function agentScopeCountLabel(scope, count)", content)
        label_fn = content.split("function agentScopeCountLabel(scope, count)", 1)[1].split(
            "function availableTypes()",
            1,
        )[0]
        self.assertIn("if (!isCrossSpaceScope(scope)) return String(count);", label_fn)
        self.assertIn("if (crossSpaceDataCoversScope(scope)) return String(count);", label_fn)
        self.assertIn('return crossSpaceLoadingCoversScope(scope) ? "..." : "";', label_fn)
        self.assertIn("function requestedCrossSpaceScope(scope)", content)
        self.assertIn('return scope === "mine" ? "mine" : "all";', content)
        self.assertIn("function crossSpaceDataCoversScope(scope)", content)
        self.assertIn("function mergeAgentLists(existingAgents, incomingAgents)", content)
        self.assertIn("if (isCrossSpaceScope(state.agentScopeFilter) && !crossSpaceDataCoversScope(state.agentScopeFilter)) return [];", content)
        self.assertIn("const countLabel = agentScopeCountLabel(scope, count);", content)
        self.assertIn("if (countLabel) {", content)
        self.assertIn("isCrossSpaceScope(scope) && !crossSpaceDataCoversScope(scope) && !state.crossSpaceLoading", content)
        self.assertIn("state.count = payload.count ?? (isCrossSpaceScope(payloadScope) ? payloadItems.length : state.agents.length);", content)
        self.assertIn("state.total = payload.total ?? state.count;", content)
        self.assertIn("let shouldLoadActiveScope = false;", content)
        self.assertIn("shouldLoadActiveScope = isCrossSpaceScope(activeScope) && !crossSpaceDataCoversScope(activeScope);", content)
        self.assertIn("} finally {", content)
        self.assertIn("void loadCrossSpaceAgents(state.agentScopeFilter);", content)
        self.assertIn("view_scope: viewScope", content)

    def test_space_navigator_preserves_hitl_draft_and_supports_approval(self):
        content = (WIDGET_DIR / "space-navigator.html").read_text(encoding="utf-8")

        self.assertIn("async function refreshSpaceDraftStatus(draftId)", content)
        self.assertIn('action: "get_draft", draft_id: draftId', content)
        self.assertIn("function renderSpaceDraftView(root)", content)
        self.assertIn('action: "approve_draft"', content)
        self.assertIn('action: "reject_draft"', content)
        self.assertIn("spaceDraftEditableChanges", content)
        self.assertIn("if (state.draft) renderSpaceDraftView(panel);", content)

    def test_space_navigator_allows_space_type_edit_before_approval(self):
        content = (WIDGET_DIR / "space-navigator.html").read_text(encoding="utf-8")

        self.assertIn("function spaceDraftModeOptions()", content)
        self.assertIn('data-testid": "spaces-draft-mode"', content)
        self.assertIn('changes["space_mode"] = nextMode;', content)
        self.assertIn("coerceSpaceVisibilityForMode(nextMode", content)

    def test_space_navigator_has_discover_and_invite_join_surface(self):
        content = (WIDGET_DIR / "space-navigator.html").read_text(encoding="utf-8")

        self.assertIn("function renderDiscoverView(wrapper)", content)
        self.assertIn('action: "discover"', content)
        self.assertIn('action: "join_invite"', content)
        self.assertIn('action: "join_public"', content)
        self.assertIn('action: "create_invite"', content)
        self.assertIn("spaces-invite-code", content)
        self.assertIn("spaces-create-invite", content)

    def test_space_navigator_invite_copy_and_member_name_fallbacks(self):
        content = (WIDGET_DIR / "space-navigator.html").read_text(encoding="utf-8")

        self.assertIn("function copyInviteCode(inviteCode)", content)
        self.assertIn('data-testid": "spaces-copy-invite"', content)
        self.assertIn("navigator.clipboard.writeText", content)
        self.assertIn("function memberDisplayName(member)", content)
        self.assertIn("member?.display_name", content)
        self.assertIn("member?.name", content)

    def test_context_explorer_uses_immersive_artifact_mode_for_selected_uploads(self):
        content = (WIDGET_DIR / "context-explorer.html").read_text(encoding="utf-8")

        self.assertIn("function renderArtifactPanel()", content)
        self.assertIn('body.artifact-mode', content)
        self.assertNotIn('className: "artifact-back"', content)
        self.assertNotIn('className: "detail-back"', content)
        self.assertIn('aria-label": "Refresh context list"', content)
        self.assertIn("document.body.classList.toggle(\"artifact-mode\", Boolean(selectedUpload));", content)
        self.assertIn("if (selectedUpload) {\n        root.appendChild(renderArtifactPanel());", content)
        self.assertLess(
            content.index("if (selectedUpload) {"),
            content.index("renderHero(root);"),
        )

    def test_context_explorer_treats_vault_items_as_permanent(self):
        content = (WIDGET_DIR / "context-explorer.html").read_text(encoding="utf-8")

        self.assertIn("function hasExpiry(item)", content)
        self.assertIn("if (isVaultItem(item)) return false;", content)
        self.assertIn("function contextExpiryLabel(item)", content)
        self.assertIn('storage === "workspace_intelligence"', content)
        self.assertIn('if (isVaultItem(item)) return "Permanent";', content)
        self.assertIn("if (hasExpiry(item))", content)
        self.assertIn("items.filter(isExpiringItem).length", content)
        self.assertNotIn("if (item.ttl || item.expires_at)", content)
        self.assertNotIn('item.expires_at ? ttlLabel(item) : "Permanent"', content)

    def test_context_explorer_opens_only_raster_uploads_in_new_tab(self):
        content = (WIDGET_DIR / "context-explorer.html").read_text(encoding="utf-8")

        self.assertIn("function isRasterImageContentType(contentType)", content)
        self.assertIn('"image/png"', content)
        self.assertIn('"image/jpeg"', content)
        self.assertNotIn('"image/jpg"', content)
        self.assertIn("function downloadUploadUrl(url, filename)", content)
        self.assertIn('link.target = "_blank"', content)
        self.assertIn("MAX_UPLOAD_OBJECT_URL_CACHE_ENTRIES = 16", content)
        self.assertIn("URL.revokeObjectURL(oldestObjectUrl)", content)
        self.assertIn("revokeAfterClick", content)
        self.assertIn("Auth-gated URLs fail closed", content)
        self.assertIn("function handleUploadAction(uploadValue)", content)
        self.assertIn("if (isRasterImageContentType(uploadValue.content_type))", content)
        self.assertIn("downloadUploadUrl(uploadValue.url, uploadValue.filename);", content)
        self.assertIn('isRasterImageContentType(uploadValue.content_type) ? "Open full image" : "Download file"', content)

    def test_context_explorer_renders_pdf_and_sanitizes_markdown_with_allowlist(self):
        content = (WIDGET_DIR / "context-explorer.html").read_text(encoding="utf-8")

        self.assertIn('frame.setAttribute("sandbox", "allow-scripts")', content)
        self.assertIn("const MARKDOWN_ALLOWED_TAGS = new Set", content)
        self.assertIn("function sanitizeMarkdownFragment(frag)", content)
        self.assertIn("if (!MARKDOWN_ALLOWED_TAGS.has(tag))", content)
        self.assertIn('el.replaceWith(document.createTextNode(el.textContent || ""))', content)
        self.assertIn("function isUnsafeMarkdownUrl(value)", content)
        self.assertIn('replace(/[\\u0000-\\u001F\\u007F\\s]+/g, "")', content)
        self.assertIn('normalized.startsWith("javascript:")', content)
        self.assertIn('normalized.startsWith("data:")', content)
        self.assertIn("blob: is allowed only for this page's object URLs", content)
        self.assertIn("MARKDOWN_ALLOWED_URL_ATTRS.has(attr)", content)
        self.assertIn('el.setAttribute("target", "_blank")', content)
        self.assertIn('el.setAttribute("rel", "noopener noreferrer")', content)
        self.assertIn('className: "artifact-pdf-viewer"', content)
        self.assertIn("Chrome's native PDF viewer", content)
        self.assertIn("HTML artifacts remain sandboxed", content)
        self.assertIn('frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-downloads")', content)
        self.assertNotIn("allow-top-navigation", content)
        self.assertIn('frame.setAttribute("referrerpolicy", "no-referrer")', content)
        self.assertIn("function blobLooksLikePdf(blob)", content)
        self.assertIn('throw new Error("Upload payload is not a PDF document")', content)
        self.assertIn('throw new Error("PDF source must be hydrated as a blob URL")', content)
        self.assertIn("function hydratePdfSource(frame, url, contentType)", content)
        self.assertIn("hydratePdfSource never falls back to the original upload URL", content)
        self.assertIn("hydratePdfSource(frame, uploadValue.url, uploadValue.content_type)", content)
        self.assertNotIn("hydrateUploadSource(frame, uploadValue.url, uploadValue.content_type)", content)
        self.assertIn('openUploadUrl(uploadValue.url)', content)
        self.assertIn('downloadUploadUrl(uploadValue.url, uploadValue.filename)', content)
        self.assertNotIn("PDF preview is kept out of the live widget sandbox", content)
        self.assertNotIn('className: "artifact-file-card artifact-pdf-card"', content)
        self.assertNotIn('iframe", { className: "upload-pdf"', content)
        self.assertIn("This context item is not a file upload.", content)
        self.assertIn("function hasExplicitUploadMetadata(value)", content)
        self.assertNotIn("function hasUploadMetadata(value)", content)
        self.assertIn("Keep this list in sync with _EXPLICIT_FILE_UPLOAD_METADATA_KEYS", content)

        allowed_tags_match = re.search(
            r"const MARKDOWN_ALLOWED_TAGS = new Set\(\[(.*?)\]\);",
            content,
            re.DOTALL,
        )
        self.assertIsNotNone(allowed_tags_match)
        allowed_tags = set(re.findall(r'"([^"]+)"', allowed_tags_match.group(1)))
        for unsafe_tag in {
            "animate",
            "embed",
            "foreignobject",
            "form",
            "iframe",
            "link",
            "math",
            "meta",
            "object",
            "script",
            "set",
            "style",
            "svg",
            "use",
        }:
            self.assertNotIn(unsafe_tag, allowed_tags)

    def test_context_explorer_upload_metadata_keys_match_context_tool(self):
        widget = (WIDGET_DIR / "context-explorer.html").read_text(encoding="utf-8")
        context_tool = CONTEXT_TOOL.read_text(encoding="utf-8")

        py_match = re.search(
            r"_EXPLICIT_FILE_UPLOAD_METADATA_KEYS = frozenset\(\s*(\{.*?\})\s*\)",
            context_tool,
            re.DOTALL,
        )
        self.assertIsNotNone(py_match)
        py_keys = set(ast.literal_eval(py_match.group(1)))

        js_match = re.search(
            r"function hasExplicitUploadMetadata\(value\).*?return \[(.*?)\]\.some",
            widget,
            re.DOTALL,
        )
        self.assertIsNotNone(js_match)
        js_keys = set(re.findall(r'"([^"]+)"', js_match.group(1)))

        self.assertEqual(js_keys, py_keys)

    def test_context_explorer_renders_html_context_values_in_sandbox(self):
        content = (WIDGET_DIR / "context-explorer.html").read_text(encoding="utf-8")

        self.assertIn("function isHtmlValue(value)", content)
        self.assertIn("function htmlValueFor(value)", content)
        self.assertIn('normalized === "html"', content)
        self.assertIn('normalized === "text/html"', content)
        self.assertIn("function shouldRenderHtml()", content)
        self.assertIn("ctx.allow_html_render === false", content)
        self.assertIn('className: "html-card-chrome"', content)
        self.assertIn("Sandboxed HTML", content)
        self.assertIn('frame.setAttribute("sandbox", "allow-scripts")', content)
        self.assertIn('frame.setAttribute("referrerpolicy", "no-referrer")', content)
        self.assertIn("frame.srcdoc = htmlValue.html", content)
        self.assertIn('m.type !== "ax-resize"', content)
        self.assertIn("e.source !== iframe.contentWindow", content)
        self.assertIn("Math.min(1200, Math.max(100", content)
        self.assertIn("htmlMessageAbortController: null", content)
        self.assertIn("function resetHtmlMessageListener()", content)
        self.assertIn("state.htmlMessageAbortController.abort()", content)
        self.assertIn("signal: state.htmlMessageAbortController.signal", content)

    def test_context_explorer_hides_internal_storage_source_labels(self):
        content = (WIDGET_DIR / "context-explorer.html").read_text(encoding="utf-8")

        self.assertIn("function publicSourceLabel(value)", content)
        self.assertIn('"redis"', content)
        self.assertIn('"postgres"', content)
        self.assertIn("Internal storage/provenance labels are diagnostics", content)
        self.assertIn("publicSourceLabel(item.source)", content)
        self.assertNotIn('"Source", item.source', content)
        self.assertNotIn('"source: " + item.source', content)

    def test_context_explorer_renders_human_friendly_artifact_rows(self):
        content = (WIDGET_DIR / "context-explorer.html").read_text(encoding="utf-8")

        self.assertIn("function renderEntryThumb(item, uploadValue, htmlValue)", content)
        self.assertIn('className: "entry-preview-thumb"', content)
        self.assertIn("hydrateUploadImage(img, uploadValue.url, uploadValue.content_type)", content)
        self.assertIn("itemArtifactGlyph(item)", content)
        self.assertIn("function uploadValueFor(value)", content)
        self.assertIn("function htmlValueFor(value)", content)
        self.assertIn("item?.file_upload && typeof item.file_upload === \"object\"", content)
        self.assertIn("type: item.file_upload.type || \"file_upload\"", content)
        self.assertIn("function itemArtifactKindLabel(item)", content)
        self.assertIn("function contentTypeFromFilename(value)", content)
        self.assertIn("function filenameFromContextKey(key)", content)
        self.assertIn('token.trim().split("?")[0].split("#")[0]', content)
        self.assertIn("function uploadValueFromContextKey(key, value)", content)
        self.assertIn("const filename = filenameFromContextKey(key)", content)
        self.assertIn("if (inferredUpload) item.value = inferredUpload", content)
        self.assertIn('"mime_type"', content)
        self.assertIn('"resource_mime_type"', content)
        self.assertIn("function uploadKindLabel(uploadValue)", content)
        self.assertIn("function uploadObjectUrlCacheKey(url, contentType)", content)
        self.assertIn("function closeHtmlFullscreenOverlays(options)", content)
        self.assertIn("function contextKindLabel(uploadValue, htmlValue, item)", content)
        self.assertIn("function isInlineTextUploadContentType(contentType)", content)
        self.assertIn('"image/svg+xml"', content)
        self.assertIn('if (htmlValue) return "HTML";', content)
        self.assertIn("function publicActorLabel(value)", content)
        self.assertIn("rowTitle || item.key || \"untitled\"", content)
        self.assertIn('"by " + actor', content)
        self.assertIn("return shorten(item.summary, 140)", content)
        self.assertIn('return "Sandboxed HTML document";', content)

    def test_game_board_renders_game_code_and_events_views(self):
        content = (WIDGET_DIR / "game-board.html").read_text(encoding="utf-8")

        self.assertIn("function gameTitle()", content)
        self.assertIn('if (gameKey === "tic_tac_toe") return "Tic-Tac-Toe";', content)
        self.assertIn('if (gameKey === "ax_trivia") return "Commonflame Trivia";', content)
        self.assertIn("function safeSourceHref(value)", content)
        self.assertIn('url.protocol === "http:" || url.protocol === "https:" ? url.href : null', content)
        self.assertIn('host.callServerTool("games"', content)
        self.assertIn('"move"', content)
        self.assertIn('const labels = { game: "Game", source: "Source", code: "<> Code", events: "Events" };', content)
        self.assertIn("function renderBoard(root)", content)
        self.assertIn("function renderTrivia(root)", content)
        self.assertIn("function renderSource(root)", content)
        self.assertIn("function appendMarkdownArticle(root, text)", content)
        self.assertIn('action: "answer"', content)
        self.assertIn("function renderCode(root)", content)
        self.assertIn("function renderEvents(root)", content)
        self.assertIn("return app.callServerTool({ name, arguments: args });", content)


if __name__ == "__main__":
    unittest.main()
