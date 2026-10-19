import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
from state_store import StateStore, project_key
from validator import validate_charter_integrity, validate_fake_success
from atomic_io import compute_sha256
from hub_runtime import ingest_jsonl


class SystemFirstTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'workspace-テスト'
        self.root.mkdir()
        (self.root / '.git').mkdir()
        (self.root / 'docs' / 'ai-continuity').mkdir(parents=True)
        self.old = os.environ.get('CCG_STATE_ROOT')
        os.environ['CCG_STATE_ROOT'] = str(Path(self.tmp.name) / 'CCG state with spaces')

    def tearDown(self):
        if self.old is None:
            os.environ.pop('CCG_STATE_ROOT', None)
        else:
            os.environ['CCG_STATE_ROOT'] = self.old
        self.tmp.cleanup()

    def test_team_off_by_default_and_isolated(self):
        store = StateStore(self.root)
        other = StateStore(self.root.parent / 'other-project')
        self.assertFalse(store.chat_enabled())
        self.assertFalse(other.chat_enabled())
        store.set_chat_enabled(True)
        self.assertTrue(StateStore(self.root).chat_enabled())
        self.assertFalse(other.chat_enabled())
        store.set_chat_enabled(False)
        self.assertFalse(store.chat_enabled())

    def test_departments_have_no_presets_and_are_project_scoped(self):
        store = StateStore(self.root)
        other = StateStore(self.root.parent / 'other-project')
        self.assertEqual(store.departments(), [])
        store.post_message('general', 'operator', 'operator', 'General update')
        self.assertEqual(store.departments(), [])
        for department in ('research', 'dept-6', 'dept-1'):
            store.post_message(department, 'sol', 'sol', 'Project update')
        self.assertEqual(StateStore(self.root).departments(), ['dept-1', 'dept-6', 'research'])
        self.assertEqual(other.departments(), [])
        self.assertEqual(len(store.messages('all')), 4)

    def test_invalid_department_still_rejected(self):
        store = StateStore(self.root)
        for department in ('', 'all', '../project', 'has spaces', 'x' * 65):
            with self.subTest(department=department):
                with self.assertRaises(ValueError):
                    store.post_message(department, 'sol', 'sol', 'Update')
        for department in ('', '../project', 'has spaces', 'x' * 65):
            with self.subTest(query=department):
                with self.assertRaises(ValueError):
                    store.messages(department)

    def test_jsonl_general_and_custom_departments(self):
        store = StateStore(self.root)
        message = json.dumps({'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': 'Observed message'}})
        for department in ('general', 'research', 'dept-6'):
            stat = ingest_jsonl(store, io.StringIO(message), department=department,
                                role='sol', run_id='run-' + department)
            self.assertEqual(stat['agent_messages'], 1)
            self.assertEqual(len(store.messages(department)), 1)

    def test_cli_accepts_project_specific_departments(self):
        script = str(SCRIPT_DIR / 'hub_runtime.py')
        StateStore(self.root).set_chat_enabled(True)
        for department in ('general', 'research', 'dept-6'):
            result = subprocess.run([sys.executable, script, '--project', str(self.root),
                'message', '--department', department, '--role', 'operator',
                '--author', 'operator', '--text', 'Project update'],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, script, '--project', str(self.root),
            'observe', '--department', 'general', '--role', 'sol', '--run-id', 'cli-run'],
            input=json.dumps({'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': 'Observed via CLI'}}),
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['agent_messages'], 1)

    def test_messages_idempotent_and_acked(self):
        store = StateStore(self.root)
        store.set_chat_enabled(True)
        one = store.post_message('dept-1','sol','sol-1','Changed the API',
                                 recipients=('sol-2',),provenance='public-run',origin_id='run-1:3')
        two = store.post_message('dept-1','sol','sol-1','Changed the API',
                                 recipients=('sol-2',),provenance='public-run',origin_id='run-1:3')
        self.assertEqual(one, two)
        self.assertEqual(len(store.messages('dept-1')), 1)
        self.assertEqual(len(store.mailbox('sol-2')), 1)
        self.assertTrue(store.acknowledge(one, 'sol-2'))
        self.assertFalse(store.acknowledge(one, 'sol-2'))
        self.assertFalse(store.mailbox('sol-2'))
        store.set_chat_enabled(False)
        self.assertEqual(len(store.messages('dept-1')), 1)

    def test_lease_fencing(self):
        store = StateStore(self.root)
        epoch = store.claim('task-17', 'sol-1')
        self.assertIsNotNone(epoch)
        self.assertIsNone(store.claim('task-17', 'sol-2'))
        self.assertFalse(store.release('task-17','sol-1',epoch + 1))
        self.assertTrue(store.has_lease('task-17','sol-1',epoch))
        self.assertTrue(store.release('task-17','sol-1',epoch))
        self.assertFalse(store.has_lease('task-17','sol-1',epoch))
        self.assertGreater(store.claim('task-17','sol-2'), epoch)

    def test_jsonl_ingest_only_real_messages(self):
        store = StateStore(self.root)
        stream=io.StringIO('\n'.join([
            json.dumps({'type':'item.completed','item':{'type':'command_execution','text':'not a Sol utterance'}}),
            json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'The code compiles.'}}),
            json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':75,'output_tokens':11}}),
            '{broken',
        ]))
        stat=ingest_jsonl(store, stream, department='dept-2', role='sol',run_id='r-1')
        self.assertEqual(stat['agent_messages'],1)
        self.assertEqual(len(store.messages('dept-2')),1)
        self.assertEqual(store.messages('dept-2')[0]['body'],'The code compiles.')
        self.assertEqual(store.messages('dept-2')[0]['provenance'],'codex-exec-json')
        stream=io.StringIO(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'The code compiles.'}}))
        ingest_jsonl(store,stream,department='dept-2',role='sol',run_id='r-1')
        # New origin line 1 is different from old message line 2 -> legitimate new evidence.
        self.assertEqual(len(store.messages('dept-2')),2)

    def test_self_hash_cannot_approve_charter(self):
        body='My goal\n'
        charter=self.root/'docs'/'ai-continuity'/'PROJECT_CHARTER.md'
        charter.write_text('---\napproved_sha256: "'+compute_sha256(body.strip())+'"\napproved_by: USER\n---\n'+body,encoding='utf8')
        self.assertIn('CHARTER_UNAPPROVED',{x.code for x in validate_charter_integrity(self.root)})
        from approval_guard import approval_path
        approval_path(self.root).parent.mkdir(parents=True, exist_ok=True)
        approval_path(self.root).write_text(json.dumps({
            'state':'APPROVED','project_id':project_key(self.root),
            'approved_sha256':compute_sha256(charter.read_text(encoding='utf8'))}),encoding='utf8')
        self.assertFalse(validate_charter_integrity(self.root))
        new_body='Different goal\n'
        charter.write_text('---\napproved_sha256: "'+compute_sha256(new_body.strip())+'"\napproved_by: USER\n---\n'+new_body,encoding='utf8')
        self.assertIn('CHARTER_APPROVAL_MISMATCH',{x.code for x in validate_charter_integrity(self.root)})

    def test_fake_success_status_and_missing_evidence(self):
        current=self.root/'docs'/'ai-continuity'/'CURRENT.md'
        evidence=self.root/'docs'/'ai-continuity'/'EVIDENCE_INDEX.md'
        current.write_text('---\ncurrent_task: "T91"\nstatus: "PASS"\n---\nCompleted',encoding='utf8')
        self.assertIn('UNVERIFIED_SUCCESS_DECLARATION',{x.code for x in validate_fake_success(self.root)})
        evidence.write_text('| T91 | PASS | Need proof | proof/does-not-exist.json |\n',encoding='utf8')
        self.assertIn('EVIDENCE_MISSING',{x.code for x in validate_fake_success(self.root)})

    def test_machine_receipt_for_pass_and_log_tamper(self):
        from evidence_runner import run_and_record
        current=self.root/'docs'/'ai-continuity'/'CURRENT.md'
        current.write_text('---\ncurrent_task: "T99"\nstatus: "PASS"\n---\nOK', encoding='utf8')
        receipt=run_and_record(self.root, 'T99', [sys.executable, '-c', 'print("verified")'])
        self.assertEqual(receipt['result'],'PASS')
        self.assertFalse([x for x in validate_fake_success(self.root) if x.level=='ERROR'])
        log=self.root/receipt['output_path']
        log.write_text('tampered',encoding='utf8')
        self.assertIn('EVIDENCE_INVALID', {x.code for x in validate_fake_success(self.root)})

    def test_ten_processes_thousand_messages_no_loss(self):
        store = StateStore(self.root)
        store.set_chat_enabled(True)
        code = ("import sys;from pathlib import Path;from state_store import StateStore;"
                "s=StateStore(Path(sys.argv[1]));i=int(sys.argv[2]);"
                "[(s.post_message('dept-1','sol',f'sol-{i}',f'message-{i}-{j}',"
                "provenance='multi-test',origin_id=f'{i}-{j}')) for j in range(100)]")
        env=os.environ.copy()
        env['PYTHONPATH']=str(SCRIPT_DIR) + os.pathsep + env.get('PYTHONPATH','')
        procs=[subprocess.Popen([sys.executable,'-c',code,str(self.root),str(i)],env=env,
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for i in range(10)]
        for proc in procs:
            stdout,stderr=proc.communicate(timeout=40)
            self.assertEqual(proc.returncode,0,stderr+stdout)
        with store.connection() as db:
            n=db.execute('SELECT COUNT(*) FROM messages WHERE project_id=?',(store.project_id,)).fetchone()[0]
        self.assertEqual(n,1000)

    def test_ten_process_checkpoint_and_handoff_unique(self):
        script = str(SCRIPT_DIR/'continuity.py')
        env = os.environ.copy()
        agents=[]
        for i in range(10):
            agents.append(subprocess.Popen([sys.executable,script,'checkpoint','--task','TASK-X',
                                             '--agent',f'Sol-{i}'], cwd=self.root,env=env,
                                            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
        for p in agents:
            out,err = p.communicate(timeout=30)
            self.assertEqual(p.returncode,0,err+out)
        store=StateStore(self.root)
        self.assertEqual(len(store.checkpoints()),10)
        handoffs=list((self.root/'docs'/'ai-continuity'/'HANDOFFS').glob('handoff_*.md'))
        self.assertEqual(len(handoffs),10)
        self.assertEqual(len({x.name for x in handoffs}),10)


if __name__=='__main__':
    unittest.main()
