#!/usr/bin/env python3
"""Hermetic checks for agent identity isolation, scope, and uncertain writes."""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('local_agents', Path(__file__).with_name('local-agents.py'))
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)

class HostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.identity = dict(origin='http://localhost:3000', resource='http://localhost:3000/mcp', token_endpoint='http://localhost:3000/oauth/token', client_id='client', agent_id='planner-id', agent_name='planner-handle', space_id='space', access_token='private-access-fixture', refresh_token='private-refresh-fixture', expires_at=time.time() + 1000)
        host.atomic(self.home / 'planner.json', self.identity)
        host.atomic(self.home / 'bridge.json', {**self.identity, 'agent_id': 'bridge-id'})

    def test_loopback_only(self):
        for url in ['https://example.com', 'http://localhost.evil:3000', 'http://u:p@localhost', 'http://localhost/x']:
            with self.assertRaises(host.Failure): host.local_origin(url)
        self.assertEqual(host.local_origin('http://localhost:3000'), 'http://localhost:3000')

    def test_private_atomic_state(self):
        self.assertEqual((self.home / 'planner.json').stat().st_mode & 0o777, 0o600)
        target = self.home / 'link.json'
        target.symlink_to(self.home / 'planner.json')
        with self.assertRaises(host.Failure): host.atomic(target, {})

    def test_expected_shared_workspace_blocks_old_credentials(self):
        host.atomic(self.home / 'installation.json', {'space_id': 'intended-lab'})
        with self.assertRaises(host.Failure): host.Actor(self.home, 'planner').load()

    def test_identity_mismatch_blocks(self):
        with patch.object(host.Actor, 'tool', return_value={'data': {'id': 'other-agent', 'space_id': 'space'}}):
            with self.assertRaises(host.Failure): host.Actor(self.home, 'planner').verify()

    def test_refresh_rotates_atomically(self):
        self.identity['expires_at'] = 0
        host.atomic(self.home / 'planner.json', self.identity)
        with patch.object(host, 'request', return_value=(200, {'access_token': 'new-access', 'refresh_token': 'new-refresh', 'expires_in': 900})) as api, patch.object(host, 'raw_tool', return_value={'data': {}}):
            host.Actor(self.home, 'planner').tool('whoami', {'action': 'get'})
            self.assertEqual(api.call_count, 1)
        state = host.read(self.home / 'planner.json')
        self.assertEqual(state['refresh_token'], 'new-refresh')
        self.assertFalse(state['uncertain_refresh'])

    def test_uncertain_refresh_never_replayed(self):
        self.identity['expires_at'] = 0
        host.atomic(self.home / 'planner.json', self.identity)
        with patch.object(host, 'request', side_effect=host.Failure('transport')) as api:
            for _ in range(2):
                with self.assertRaises(host.Failure): host.Actor(self.home, 'planner').tool('whoami', {'action': 'get'})
            self.assertEqual(api.call_count, 1)
        self.assertTrue(host.read(self.home / 'planner.json')['uncertain_refresh'])

    def test_worker_only_accepts_assigned_tasks_or_direct_trusted_messages(self):
        class FakeActor:
            def tool(self, name, args):
                if name == 'tasks' and args['action'] == 'list':
                    return {'data': {'items': [{'id': 'mine', 'assignee': {'id': 'planner-id'}}, {'id': 'other', 'assignee': {'id': 'other-id'}}]}}
                if name == 'tasks': return {'data': {'task': {'id': 'mine', 'title': 'Plan', 'description': 'bounded'}}}
                return {'data': {'messages': [
                    {'id': 'direct', 'content': '@planner-handle help', 'agent_id': 'bridge-id', 'sender_type': 'agent'},
                    {'id': 'loop', 'content': '@planner-handle help', 'author_id': 'other-agent', 'sender_type': 'agent'},
                    {'id': 'human', 'content': '@planner-handle help', 'sender_type': 'human'},
                    {'id': 'partial', 'content': '@planner-handle-suffix help', 'author_id': 'bridge-id'},
                    {'id': 'ambient', 'content': 'hello world', 'sender_type': 'human'}]}}
        items = list(host.work_items(argparse.Namespace(home=self.home), FakeActor(), self.identity))
        self.assertEqual({item['key'] for item in items}, {'task-mine', 'message-direct', 'message-human'})

    def test_independent_same_workspace_identities_required(self):
        rows = [self.identity, {**self.identity, 'agent_id': 'reviewer-id'}, {**self.identity, 'agent_id': 'bridge-id', 'space_id': 'foreign'}]
        with patch.object(host.Actor, 'verify', side_effect=rows):
            with self.assertRaises(host.Failure): host.roles_match(self.home)

    def test_handoff_posts_new_message_and_assigns_review(self):
        host.atomic(self.home / 'jobs/planner/task-task-id.json', {'stage': 'completed', 'answer': 'Checklist', 'reply_id': 'planner-reply'})
        actors = {'reviewer': {'agent_name': 'reviewer-handle', 'agent_id': 'reviewer-id'}}
        calls = []
        def tool(actor, name, args):
            calls.append((name, args))
            if name == 'messages': return {'data': {'sent': {'id': 'handoff-id'}}}
            return {'data': {'task': {'id': 'task-id', 'requirements': {}}}}
        args = argparse.Namespace(home=self.home, task_id='task-id', retry_reviewed_rejection=False)
        with patch.object(host, 'roles_match', return_value=actors), patch.object(host.Actor, 'tool', tool), patch('builtins.print'):
            host.handoff(args)
        sent = next(args for name, args in calls if name == 'messages')
        self.assertNotIn('reply_to', sent)
        update = calls[-1][1]
        self.assertEqual(update['assignee_id'], 'reviewer-id')
        self.assertEqual(update['requirements']['local_agents_reply_to'], 'handoff-id')
        with patch.object(host, 'roles_match', return_value=actors):
            with self.assertRaises(host.Failure): host.handoff(args)

    def test_handoff_does_not_retry_uncertain_write(self):
        host.atomic(self.home / 'jobs/planner/task-task-id.json', {'stage': 'completed', 'answer': 'Checklist', 'reply_id': 'planner-reply'})
        host.atomic(self.home / 'handoffs/task-id.json', {'stage': 'needs_review'})
        args = argparse.Namespace(home=self.home, task_id='task-id', retry_reviewed_rejection=True)
        with patch.object(host, 'roles_match', return_value={}), patch.object(host.Actor, 'tool') as api:
            with self.assertRaises(host.Failure): host.handoff(args)
            api.assert_not_called()

    def test_handoff_reconciles_worker_status_race_without_replaying(self):
        host.atomic(self.home / 'jobs/planner/task-task-id.json', {'stage': 'completed', 'answer': 'Checklist', 'reply_id': 'planner-reply'})
        calls = []
        actors = {'reviewer': {'agent_name': 'reviewer-handle', 'agent_id': 'reviewer-id'}}
        def tool(actor, name, args):
            calls.append((name, args['action']))
            if name == 'messages': return {'data': {'sent': {'id': 'handoff-id'}}}
            if args['action'] == 'update': raise host.Failure('read-back status advanced')
            task = {'id': 'task-id', 'requirements': {}}
            if len(calls) > 1:
                task.update(assignee={'id': 'reviewer-id'}, status='in_progress', requirements={'local_agents_reply_to': 'handoff-id'})
            return {'data': {'task': task}}
        args = argparse.Namespace(home=self.home, task_id='task-id', retry_reviewed_rejection=False)
        with patch.object(host, 'roles_match', return_value=actors), patch.object(host.Actor, 'tool', tool), patch('builtins.print'):
            host.handoff(args)
        self.assertEqual(calls.count(('messages', 'send')), 1)
        self.assertEqual(calls.count(('tasks', 'update')), 1)
        receipt = host.read(self.home / 'handoffs/task-id.json')
        self.assertEqual(receipt['stage'], 'queued')
        self.assertEqual(receipt['reconciled_status'], 'in_progress')

    def test_handoff_reconciliation_accepts_only_exact_saved_assignment(self):
        saved = {'assignee': {'id': 'reviewer'}, 'requirements': {'local_agents_reply_to': 'handoff'}, 'status': 'in_progress'}
        host.confirm_handoff(saved, 'reviewer', 'handoff')
        for reviewer, handoff in [('other', 'handoff'), ('reviewer', 'other')]:
            with self.assertRaises(host.Failure): host.confirm_handoff(saved, reviewer, handoff)

    def test_rpc_errors_do_not_return_success(self):
        with patch.object(host, 'request', return_value=(200, {'error': {'code': -1}})):
            with self.assertRaises(host.Failure): host.raw_tool('http://localhost:3000', 'fixture', 'tasks', {})

    def test_no_receipt_means_no_confirmed_write(self):
        with self.assertRaises(host.Failure): host.message_id({'data': {'status': 'accepted'}})
        with self.assertRaises(host.Failure): host.task_value({'data': {'task': {}}})

if __name__ == '__main__': unittest.main()
