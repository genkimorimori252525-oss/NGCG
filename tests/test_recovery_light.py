"""Recovery is a bounded index; source retrieval must remain lossless and explicit."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loader import build_recovery_packet, load_evidence


class LightRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.docs = self.root / 'docs/ai-continuity'
        self.docs.mkdir(parents=True)
        (self.docs / 'PROJECT_CHARTER.md').write_text(
            '# Goal\nKeep data local.\n# Constraints\nNever upload private records.\n', encoding='utf-8')
        (self.docs / 'CURRENT.md').write_text(
            '---\ncurrent_task: CCG_Mixed-id\nstatus: UNKNOWN\n---\nNext: fix parser.\n', encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def packet(self, **kwargs):
        with patch('loader.load_git_state', return_value={'branch':'main','head':'12345678','dirty':False}), \
             patch('loader.load_beads_state', return_value={'ready_tasks':[]}):
            return build_recovery_packet(self.root, **kwargs)

    def test_generic_ids_case_unknown_and_reordered_columns(self):
        (self.docs / 'EVIDENCE_INDEX.md').write_text(
            '| Description | Status | Task ID | Reference |\n|---|---|---|---|\n'
            '| unknown | UNKNOWN | CCG_Mixed-id | unknown.json |\n'
            '| issue | FAIL | 仕事/一 | fail.json |\n'
            '| unrecognised | WAITING_APPROVAL | task.99 | pending.json |\n'
            '| success | PASS | some-other | success.json |\n', encoding='utf-8')
        evidence = load_evidence(self.root)
        self.assertEqual([row['id'] for row in evidence['items']],
                         ['CCG_Mixed-id', '仕事/一', 'task.99', 'some-other'])
        self.assertEqual(evidence['counts']['UNKNOWN'], 2)
        self.assertEqual(evidence['counts']['FAIL'], 1)
        self.assertEqual(evidence['counts']['PASS'], 1)
        self.assertTrue(evidence['diagnostics'])
        self.assertEqual(evidence['items'][0]['line'], 3)

    def test_missing_status_and_malformed_rows_are_visible(self):
        (self.docs / 'EVIDENCE_INDEX.md').write_text(
            '| ID | Status | Description | Ref |\n|---|---|---|---|\n'
            '| no-status | | Keep uncertainty | log |\n'
            '| | FAIL | missing ID | log |\n'
            '| short-row |\n', encoding='utf-8')
        evidence = load_evidence(self.root)
        self.assertEqual(evidence['counts']['UNKNOWN'], 2)
        self.assertGreaterEqual(len(evidence['diagnostics']), 2)
        packet = self.packet()
        self.assertIn('diagnostics=', packet['text'])
        self.assertIn('HOLD', packet['text'])

    def test_budget_covers_large_sources_and_preserves_originals(self):
        (self.docs / 'CURRENT.md').write_text('---\ncurrent_task: CCG_Mixed-id\n---\n' + '履歴。' * 10000 + '\nSTOP: no launch.', encoding='utf-8')
        (self.docs / 'EVIDENCE_INDEX.md').write_text('\n'.join(
            f'| Task-{i} | UNKNOWN | Waiting {i} | log{i} |' for i in range(1000)), encoding='utf-8')
        before = {p: p.read_bytes() for p in self.docs.rglob('*') if p.is_file()}
        packet = self.packet(max_chars=2500)
        self.assertLessEqual(len(packet['text']), 2500)
        self.assertIn('UNKNOWN:1000', packet['text'])
        self.assertIn('HOLD', packet['text'])
        self.assertIn('recall', packet['text'])
        self.assertTrue(packet['deferred'])
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertIn('STOP: no launch.', self.packet(brief=False)['text'])

    def test_long_single_line_and_minimum_budget(self):
        (self.docs / 'PROJECT_CHARTER.md').write_text('NEVER ' + '秘密' * 10000, encoding='utf-8')
        packet = self.packet(max_chars=1000)
        self.assertLessEqual(len(packet['text']), 1000)
        self.assertIn('HOLD', packet['text'])
        self.assertTrue(packet['deferred'])
        with self.assertRaises(ValueError):
            self.packet(max_chars=100)

    def test_small_context_remains_verbatim_without_approval_inference(self):
        text = self.packet()['text']
        for required in ('Keep data local.', 'Never upload private records.', 'Next: fix parser.', 'UNKNOWN'):
            self.assertIn(required, text)
        self.assertIn('UNAPPROVED', text)

    def test_full_keeps_unknown_and_arbitrary_id(self):
        (self.docs / 'EVIDENCE_INDEX.md').write_text(
            '| arbitraryCase | UNKNOWN | UNCERTAINTY_TAIL | raw.log |', encoding='utf-8')
        text = self.packet(brief=False)['text']
        self.assertIn('arbitraryCase', text)
        self.assertIn('UNCERTAINTY_TAIL', text)

    def recall(self, *args):
        return subprocess.run([sys.executable, '-B', str(Path(__file__).resolve().parent.parent / 'continuity.py'),
            'recall', *args], cwd=self.root, capture_output=True, text=True, encoding='utf-8', timeout=10,
            env={**os.environ, 'PYTHONUTF8':'1'})

    def test_recall_paginates_single_line_without_loss_and_checks_hash(self):
        original = '前半' * 2000 + '\nTAIL: approval required.'
        target = self.docs / 'CURRENT.md'
        target.write_bytes(original.encode('utf-8'))
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        offset = 0
        reconstructed = ''
        while True:
            result = self.recall('CURRENT.md', '--offset', str(offset), '--max-chars', '1000', '--sha256', sha, '--json')
            self.assertEqual(result.returncode, 0, result.stderr)
            page = json.loads(result.stdout)
            self.assertLessEqual(len(result.stdout), 1000)
            self.assertNotIn('text', page)
            reconstructed += page['content']
            self.assertEqual(page['sha256'], sha)
            if page['next_offset'] is None:
                break
            self.assertGreater(page['next_offset'], offset)
            offset = page['next_offset']
        self.assertEqual(reconstructed, original)
        target.write_text(original + ' changed', encoding='utf-8')
        result = self.recall('CURRENT.md', '--sha256', sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SOURCE_CHANGED', result.stderr)

    def test_recall_exact_task_and_section_and_scope(self):
        (self.docs / 'EVIDENCE_INDEX.md').write_text(
            '| ID | Status | Description | Ref |\n|---|---|---|---|\n'
            '| CCG_Mixed-id | FAIL | one | a |\n'
            '| CCG_Mixed-id-extra | UNKNOWN | other | b |\n'
            '| CCG_Mixed-id | UNKNOWN | two | c |\n', encoding='utf-8')
        result = self.recall('EVIDENCE_INDEX.md', '--task', 'CCG_Mixed-id', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        page = json.loads(result.stdout)
        self.assertIn('one', page['content'])
        self.assertIn('two', page['content'])
        self.assertNotIn('other', page['content'])
        self.assertEqual(page['matches'], 2)
        result = self.recall('PROJECT_CHARTER.md', '--section', 'Constraints', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Never upload', json.loads(result.stdout)['content'])
        self.assertNotIn('Keep data', json.loads(result.stdout)['content'])
        self.assertNotEqual(self.recall('../outside.md').returncode, 0)
        self.assertNotEqual(self.recall('EVIDENCE_INDEX.md', '--task', 'absent').returncode, 0)

    def test_attempt_history_and_reordered_index_are_not_overwritten(self):
        from evidence_runner import run_and_record
        from validator import validate_fake_success
        index = self.docs / 'EVIDENCE_INDEX.md'
        original = ('| Description | Reference | Status | Task ID |\n|---|---|---|---|\n'
                    '| Keep original | original.log | FAIL | CCG_Mixed-id |\n')
        index.write_bytes(original.encode('utf-8'))
        before_state = os.environ.get('CCG_STATE_ROOT')
        os.environ['CCG_STATE_ROOT'] = str(self.root / 'isolated-state')
        try:
            run_and_record(self.root, 'CCG_Mixed-id', [sys.executable, '-c', 'print("verified")'])
            run_and_record(self.root, 'CCG_Mixed-id', [sys.executable, '-c', 'print("verified again")'])
            self.assertTrue(index.read_text(encoding='utf-8').startswith(original))
            rows = [item for item in load_evidence(self.root)['items'] if item['id'] == 'CCG_Mixed-id']
            self.assertEqual([item['status'] for item in rows], ['FAIL', 'PASS', 'PASS'])
            (self.docs / 'CURRENT.md').write_text(
                '---\ncurrent_task: CCG_Mixed-id\nstatus: PASS\n---\n', encoding='utf-8')
            self.assertFalse([f for f in validate_fake_success(self.root) if f.level == 'ERROR'])
        finally:
            if before_state is None:
                os.environ.pop('CCG_STATE_ROOT', None)
            else:
                os.environ['CCG_STATE_ROOT'] = before_state

    def test_compaction_uses_budgeted_index_without_full_audit(self):
        from hooks import handle_session_start
        (self.docs / 'CURRENT.md').write_text('No launch.\n' * 5000, encoding='utf-8')
        with patch('hooks.run_full_audit', side_effect=AssertionError('Unrequested full audit')):
            result = handle_session_start({'source':'compact'}, self.root)
        self.assertLessEqual(len(result['additionalContext']), 2500)
        self.assertIn('HOLD', result['additionalContext'])

    def test_recall_requires_hash_for_continuation_and_rejects_ambiguous_section(self):
        self.assertNotEqual(self.recall('CURRENT.md', '--offset', '1').returncode, 0)
        (self.docs / 'CURRENT.md').write_text('# Next\nOne\n# Next\nTwo\n', encoding='utf-8')
        result = self.recall('CURRENT.md', '--section', 'Next')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('AMBIGUOUS_SECTION', result.stderr)

    def test_amendment_inventory_is_bounded_and_bodies_remain_available(self):
        amendments = self.docs / 'AMENDMENTS'
        amendments.mkdir()
        for i in range(30):
            (amendments / f'{i:03}.md').write_text('---\nstatus: APPROVED\n---\nNever upload.\n', encoding='utf-8')
        self.assertLessEqual(len(self.packet()['text']), 2500)
        result = self.recall('AMENDMENTS', '--max-chars', '1000', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        page = json.loads(result.stdout)
        self.assertIsNotNone(page['next_offset'])
        self.assertLessEqual(len(result.stdout), 1000)
        result = self.recall('AMENDMENTS/000.md', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Never upload.', json.loads(result.stdout)['content'])

    def test_nested_example_fences_do_not_hide_actual_evidence(self):
        index = self.docs / 'EVIDENCE_INDEX.md'
        index.write_text('````markdown\n```\n| EXAMPLE | PASS | Example | fake |\n```\n````\n'
                         '| ActualTask | UNKNOWN | Must retain | real |\n', encoding='utf-8')
        parsed = load_evidence(self.root)
        self.assertEqual([row['id'] for row in parsed['items']], ['ActualTask'])

    def test_snapshot_is_parsed_once_and_machine_state_respects_integrity(self):
        import recovery
        index = self.docs / 'EVIDENCE_INDEX.md'
        index.write_text('| CCG_Mixed-id | UNKNOWN | Old snapshot | old |', encoding='utf-8')
        original_reader = recovery.read_source
        reads = []
        def changing_reader(root, source):
            snapshot = original_reader(root, source)
            reads.append(source)
            if source == 'EVIDENCE_INDEX.md':
                index.write_text('| Replaced | PASS | Changed after snapshot | new |', encoding='utf-8')
            return snapshot
        with patch('recovery.read_source', side_effect=changing_reader):
            packet = self.packet()
        self.assertEqual(reads.count('EVIDENCE_INDEX.md'), 1)
        self.assertIn('UNKNOWN:1', packet['text'])
        self.assertIn('Old snapshot', packet['text'])
        self.assertNotIn('Changed after snapshot', packet['text'])
        self.assertEqual(packet['action_state'], 'HOLD_FOR_RELEVANT_SOURCE_REVIEW')
        (self.docs / 'PROJECT_CHARTER.md').write_text(
            '---\napproved_sha256: mismatch\n---\nNo launch.', encoding='utf-8')
        self.assertEqual(self.packet()['status'], 'INTEGRITY_ERROR')

    def test_reordered_columns_are_shared_with_completion_audit(self):
        from evidence_runner import run_and_record
        from validator import validate_fake_success
        before_state = os.environ.get('CCG_STATE_ROOT')
        os.environ['CCG_STATE_ROOT'] = str(self.root / 'isolated-state')
        try:
            receipt = run_and_record(self.root, 'CCG_Mixed-id', [sys.executable, '-c', 'print("checked")'])
            (self.docs / 'EVIDENCE_INDEX.md').write_text(
                '| Description | Task ID | Reference | Status |\n|---|---|---|---|\n'
                f'| checked | CCG_Mixed-id | {receipt["receipt_ref"]} | PASS |\n', encoding='utf-8')
            (self.docs / 'CURRENT.md').write_text('---\ncurrent_task: CCG_Mixed-id\nstatus: PASS\n---\n', encoding='utf-8')
            self.assertFalse([item for item in validate_fake_success(self.root) if item.level == 'ERROR'])
        finally:
            if before_state is None:
                os.environ.pop('CCG_STATE_ROOT', None)
            else:
                os.environ['CCG_STATE_ROOT'] = before_state

    def test_json_budget_includes_escaping_and_metadata(self):
        original = '\\"\t\n' * 1000
        (self.docs / 'CURRENT.md').write_bytes(original.encode('utf-8'))
        result = self.recall('CURRENT.md', '--json', '--max-chars', '1000')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(len(result.stdout), 1000)
        page = json.loads(result.stdout)
        self.assertEqual(page['content'], original[:page['next_offset']])
        self.assertEqual(page['read_state'], 'PARTIAL')

    def test_header_like_task_id_is_not_silently_dropped(self):
        (self.docs / 'EVIDENCE_INDEX.md').write_text(
            '| ID | Status | Description | Ref |\n|---|---|---|---|\n'
            '| ID | UNKNOWN | Status | raw.log |\n', encoding='utf-8')
        rows = load_evidence(self.root)['items']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], 'ID')
        self.assertEqual(rows[0]['status'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
