import copy
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import wave
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Studio, Handler, Conflict, atomic_json, fingerprint, duration

DESKTOP = Path(__file__).resolve().parents[2]
# The engine as the app ships it (npm run stage).
ENGINE = DESKTOP / 'build/engine'
RAW = {'source_name':'测试 source', 'content':'第一段是 GUI 保存的新文稿。\n第二段讲述事情的发展。', 'total_length':38,
       'video_titles':['测试视频'], 'cover_titles':['封面'], 'cover_subtitles':['副标题'], 'golden_quotes':['开场金句']}
SCRIPT = {'actual_segments':2, 'video_titles':['测试视频'], 'segments':[{'index':i,'content':f'第{i}段的内容。','length':8,'estimated_duration':1.0,'visualizer':'image'} for i in [1,2]]}

def recording(path):
    with wave.open(str(path),'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*16000)

class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.engine=Path(self.temp.name)/'引擎 with spaces'
        (self.engine/'core/pipeline').mkdir(parents=True)
        (self.engine/'core/pipeline/steps.py').write_text('')
        (self.engine/'config.yaml').write_text('global:\n  opening_quote: false\n')
        self.studio=self.open()
        self.p=self.studio.workspace/'测试 production'
        for folder in ['text','images','voice']:(self.p/folder).mkdir(parents=True)
        atomic_json(self.p/'text/raw.json',copy.deepcopy(RAW))
        atomic_json(self.p/'text/script.json',copy.deepcopy(SCRIPT))
        atomic_json(self.p/'text/mini_summary.json',{'summary':'上下文'})
        for i in [1,2]:
            (self.p/f'images/segment_{i}.png').write_bytes(b'visual')
            recording(self.p/f'voice/voice_{i}.wav')
    def open(self):
        """The service as the app starts it: read-only engine, user data, default projects folder."""
        return Studio(self.engine, Path(self.temp.name)/'用户 data', Path(self.temp.name)/'Book2Video Studio')
    def tearDown(self): self.temp.cleanup()
    def test_project_and_media_paths_are_confined(self):
        for name in ['../escape','/tmp/escape','测试 production/../other']:
            with self.assertRaises(ValueError):self.studio.project(name)
    def test_polling_does_not_change_revision(self):
        self.assertEqual(self.studio.detail(self.p.name)['revision'],self.studio.detail(self.p.name)['revision'])
    def test_save_preserves_revision_and_invalidates_dependents(self):
        d=self.studio.detail(self.p.name);value=copy.deepcopy(RAW);value['content']='GUI 新版本。'
        after=self.studio.save(self.p.name,{'kind':'raw','value':value,'expected_hash':d['hashes']['raw']})
        self.assertIn('3',after['state']['stale']);self.assertIn('6',after['state']['stale'])
        event=after['state']['events'][0]
        self.assertEqual(json.loads((self.p/'.studio/history'/event['id']/'text/raw.json').read_text())['content'],RAW['content'])
        restored=self.studio.restore(self.p.name,{'revision':event['id']})
        self.assertEqual(restored['raw']['content'],RAW['content'])
    def test_concurrent_editor_conflict_keeps_external_output(self):
        d=self.studio.detail(self.p.name);changed=copy.deepcopy(RAW);changed['content']='外部 agent 版本'
        atomic_json(self.p/'text/raw.json',changed)
        with self.assertRaises(Conflict):self.studio.save(self.p.name,{'kind':'raw','value':RAW,'expected_hash':d['hashes']['raw']})
        self.assertEqual(json.loads((self.p/'text/raw.json').read_text())['content'],changed['content'])
    def test_saved_choices_preserve_alternatives_and_restore_previous_choices(self):
        d = self.studio.detail(self.p.name)
        value = copy.deepcopy(RAW)
        choices = {'video_titles': 2, 'cover_titles': 1, 'cover_subtitles': 2, 'golden_quotes': 1}
        for field in choices:
            value[field] = [f'{field} 原选项', f'{field} 第二项\n保留换行', f'{field} 第三项']
        after = self.studio.save(self.p.name, {'kind': 'raw', 'value': value, 'selections': choices, 'expected_hash': d['hashes']['raw']})
        for field, index in choices.items():
            self.assertEqual(after['raw'][field][0], value[field][index])
            self.assertCountEqual(after['raw'][field], value[field])
        self.assertEqual(self.studio.detail(self.p.name)['raw'], after['raw'])
        self.assertIn('1.5', after['state']['stale'])
        self.assertIn('6', after['state']['stale'])
        restored = self.studio.restore(self.p.name, {'revision': after['state']['events'][0]['id']})
        for field in choices:
            self.assertEqual(restored['raw'][field], RAW[field])
    def test_invalid_or_empty_selected_candidates_do_not_save(self):
        d = self.studio.detail(self.p.name)
        invalid = [{'video_titles': 1}, {'cover_titles': -1}, {'golden_quotes': True}, {'cover_subtitles': None}, {'unknown': 0}, []]
        for choices in invalid:
            with self.subTest(choices=choices), self.assertRaises(ValueError):
                self.studio.save(self.p.name, {'kind': 'raw', 'value': copy.deepcopy(RAW), 'selections': choices, 'expected_hash': d['hashes']['raw']})
        blank = copy.deepcopy(RAW); blank['video_titles'] = ['   ']
        with self.assertRaisesRegex(ValueError, 'selected candidate cannot be empty'):
            self.studio.save(self.p.name, {'kind': 'raw', 'value': blank, 'expected_hash': d['hashes']['raw']})
        self.assertEqual(fingerprint(self.p/'text/raw.json'), d['hashes']['raw'])
        self.assertFalse(self.studio.state(self.p)['events'])
    def test_empty_optional_candidate_groups_can_be_saved(self):
        d = self.studio.detail(self.p.name)
        value = copy.deepcopy(RAW); value['cover_subtitles'] = []; value['golden_quotes'] = []
        after = self.studio.save(self.p.name, {'kind': 'raw', 'value': value, 'selections': {'cover_subtitles': None, 'golden_quotes': None}, 'expected_hash': d['hashes']['raw']})
        self.assertEqual(after['raw']['cover_subtitles'], [])
        self.assertEqual(after['raw']['golden_quotes'], [])
    def test_external_recording_invalidates_assembly_not_cover(self):
        self.studio.detail(self.p.name)
        recording(self.p/'voice/voice_1.wav')
        d=self.studio.detail(self.p.name)
        self.assertIn('5',d['state']['stale']);self.assertNotIn('6',d['state']['stale'])
    def test_hf_visual_selection_and_delivery_refresh(self):
        script=copy.deepcopy(SCRIPT);script['segments'][0]['visualizer']='hf';atomic_json(self.p/'text/script.json',script)
        (self.p/'images/segment_1.mp4').write_bytes(b'video')
        d=self.studio.detail(self.p.name);self.assertEqual(d['segments'][0]['visual'],'images/segment_1.mp4')
        (self.p/'final_video.mp4').write_bytes(b'export')
        self.assertNotEqual(d['revision'],self.studio.detail(self.p.name)['revision'])
    def test_malformed_agent_output_releases_edit_lock(self):
        state=self.studio.state(self.p);state['agent_active']={'step':'1','indices':[]};self.studio.persist(self.p,state)
        (self.p/'text/raw.json').write_text('{incomplete')
        result=self.studio.finish_agent(self.p.name,{})
        self.assertIn('warning',result);self.assertNotIn('agent_active',result['state']);self.assertFalse(result['ready']['1'])
    def agent_started(self):
        state=self.studio.state(self.p);state['agent_active']={'step':'1','indices':[],'raw_before':fingerprint(self.p/'text/raw.json')};self.studio.persist(self.p,state)
    def test_writing_agent_manuscript_saved_under_output_is_moved_to_text(self):
        self.agent_started();(self.p/'text/raw.json').unlink()
        legacy=self.p/'output/测试/text';legacy.mkdir(parents=True);raw=copy.deepcopy(RAW);raw['content']='旧路径里的新文稿。';raw['total_length']=len(raw['content']);atomic_json(legacy/'raw.json',raw)
        result=self.studio.finish_agent(self.p.name,{})
        self.assertIsNone(result['warning']);self.assertEqual(json.loads((self.p/'text/raw.json').read_text())['content'],'旧路径里的新文稿。')
        self.assertTrue(result['ready']['1'])
    def test_writing_agent_miscounted_length_is_corrected(self):
        self.agent_started();raw=copy.deepcopy(RAW);raw['content']='模型数错了字数。';raw['total_length']=999;atomic_json(self.p/'text/raw.json',raw)
        result=self.studio.finish_agent(self.p.name,{})
        self.assertIsNone(result['warning']);self.assertEqual(result['raw']['total_length'],len('模型数错了字数。'))
    def test_writing_agent_with_combined_candidates_is_rejected(self):
        self.agent_started();raw=copy.deepcopy(RAW);raw['video_titles']='标题一\n标题二';atomic_json(self.p/'text/raw.json',raw)
        self.assertIsNotNone(self.studio.finish_agent(self.p.name,{})['warning'])
    def test_writing_agent_is_told_where_to_save_everything(self):
        (self.engine/'prompts').mkdir();(self.engine/'prompts/step1_agent.md').write_text('任务：{output_json} {text_dir} {skill_path}',encoding='utf-8')
        skill=self.engine/'skills/step1/sample_writer';skill.mkdir(parents=True);(skill/'SKILL.md').write_text('---\nname: sample_writer\ndescription: 测试\n---\n正文',encoding='utf-8')
        studio=self.open();launch=studio.prepare_agent(self.p.name,{'step':'1'})
        prompt=Path(launch['context']).read_text(encoding='utf-8')
        self.assertEqual(launch['cwd'],str(self.p))
        for expected in [str(self.p/'text/raw.json'),str(self.p/'text'),'never create an output/ folder','`python`']:self.assertIn(expected,prompt)
        self.assertTrue(Path(launch['context']).resolve().is_relative_to((self.p/'.studio/agent').resolve()))
    def test_dead_agent_lease_recovers_on_restart(self):
        state=self.studio.state(self.p);state['agent_active']={'step':'4','indices':[1]};self.studio.persist(self.p,state)
        restarted=self.open()
        self.assertFalse(restarted.busy(self.p.name));self.assertIn('4',restarted.state(self.p)['stale'])
    def test_restart_creates_new_project(self):
        source=self.engine/'source.txt';source.write_text('source')
        new=self.studio.create({'name':'Another','source':str(source),'settings':{}})
        self.assertNotEqual(new['id'],self.p.name);self.assertTrue((self.p/'text/raw.json').exists())
    def test_direct_project_and_restart_need_no_source(self):
        new = self.studio.create({'name': '自由写作'})
        p = self.studio.project(new['id'])
        self.assertEqual(self.studio.state(p)['source'], '')
        self.assertEqual(self.studio.writing_options(self.studio.state(p))['mode'], 'direct')
        settings = {'step1': {'writing_mode': 'direct', 'agent_skill': 'custom'}, 'step3': {'voice': 'chosen'}}
        self.studio.persist(p, {**self.studio.state(p), 'source': '/no-longer-existing/old-source.txt', 'settings': settings})
        restarted = self.studio.restart(p.name, {})
        self.assertEqual(self.studio.state(self.studio.project(restarted['id']))['settings'], settings)
        self.assertTrue((self.p/'text/raw.json').exists())
    def test_opening_switch_persists_and_preserves_candidates_and_segment_media(self):
        before = self.studio.detail(self.p.name)
        state = before['state']; state['settings'] = {'global': {'opening_quote': True, 'other_option': 'keep'}, 'step3': {'voice': 'chosen'}}
        state['reviewed'] = {s: 'reviewed' for s in ['1','1.5','3','4','5','6']}
        self.studio.persist(self.p, state)
        assets = [self.p/'images/segment_1.png', self.p/'voice/voice_1.wav']
        hashes = [fingerprint(p) for p in assets]
        after = self.studio.set_opening(self.p.name, {'enabled': False, 'expected': True})
        self.assertFalse(after['state']['settings']['global']['opening_quote'])
        self.assertEqual(after['raw']['golden_quotes'], RAW['golden_quotes'])
        self.assertEqual(after['hashes'], before['hashes'])
        self.assertEqual(hashes, [fingerprint(p) for p in assets])
        self.assertEqual(set(after['state']['stale']), {'5'})
        self.assertNotIn('5', after['state']['reviewed'])
        self.assertEqual(after['state']['reviewed']['3'], 'reviewed')
        self.assertEqual(after['state']['settings']['global']['other_option'], 'keep')
        self.assertEqual(after['state']['settings']['step3']['voice'], 'chosen')
        reopened = self.open()
        self.assertFalse(reopened.state(self.p)['settings']['global']['opening_quote'])
        self.assertTrue(reopened.detail(self.p.name)['ready']['3'])
        self.assertTrue(reopened.detail(self.p.name)['ready']['4'])
        self.assertFalse(reopened.set_opening(self.p.name, {'enabled': True, 'expected': False})['ready']['3'])
    def test_excluded_opening_does_not_block_body_but_keeps_its_stale_scope(self):
        state = self.studio.state(self.p); state['settings'] = {'global': {'opening_quote': True}}
        state['stale'] = {'3': {'reason':'Opening narration edit','segments':[0]}, '4': {'reason':'Visual edit','segments':[0,2]}}
        self.studio.persist(self.p, state)
        disabled = self.studio.set_opening(self.p.name, {'enabled': False})['state']
        self.assertNotIn('3', disabled['stale'])
        self.assertEqual(disabled['stale']['4']['segments'], [2])
        enabled = self.studio.set_opening(self.p.name, {'enabled': True})['state']
        self.assertEqual(enabled['stale']['3']['segments'], [0])
        self.assertEqual(enabled['stale']['4']['segments'], [0,2])
    def test_opening_switch_rejects_conflicts_running_agents_and_invalid_values(self):
        with self.assertRaises(Conflict): self.studio.set_opening(self.p.name, {'enabled': True, 'expected': True})
        for value in [None, 'false', 0]:
            with self.assertRaises(ValueError): self.studio.set_opening(self.p.name, {'enabled': value})
        state = self.studio.state(self.p); state['agent_active'] = {'step': '1'}; self.studio.persist(self.p, state)
        with self.assertRaises(Conflict): self.studio.set_opening(self.p.name, {'enabled': True})
    def test_document_mode_still_requires_existing_source(self):
        for source in ['', str(self.engine/'missing.txt')]:
            with self.assertRaisesRegex(ValueError, 'does not exist'):
                self.studio.create({'source': source, 'settings': {'step1': {'writing_mode': 'source'}}})
        source = self.engine/'source.txt'; source.write_text('input')
        new = self.studio.create({'source': str(source)})
        self.assertEqual(self.studio.writing_options(self.studio.state(self.studio.project(new['id'])))['mode'], 'source')
    def test_writing_setup_preserves_outputs_other_settings_and_review(self):
        state = self.studio.state(self.p)
        state['settings'] = {'step3': {'voice': 'chosen'}, 'step1': {'other_option': 'keep'}}
        state['reviewed']['1'] = 'reviewed'; self.studio.persist(self.p, state)
        before = self.studio.detail(self.p.name)
        previous = self.studio.writing_options(before['state'])
        options = {**previous, 'mode': 'source', 'source': str(self.engine/'source.txt')}
        after = self.studio.save_writing(self.p.name, {'options': options, 'expected': previous})
        self.assertEqual(after['hashes'], before['hashes'])
        self.assertEqual(after['state']['reviewed'], before['state']['reviewed'])
        self.assertEqual(after['state']['stale'], before['state']['stale'])
        self.assertEqual(after['state']['settings']['step3'], {'voice': 'chosen'})
        self.assertEqual(after['state']['settings']['step1']['other_option'], 'keep')
        reopened = self.open()
        self.assertEqual(reopened.writing_options(reopened.state(self.p)), options)
        with self.assertRaises(Conflict):
            reopened.save_writing(self.p.name, {'options': previous, 'expected': previous})
        state = reopened.state(self.p); state['agent_active'] = {'step': '1'}; reopened.persist(self.p, state)
        with self.assertRaises(Conflict):
            reopened.save_writing(self.p.name, {'options': options})
    def test_unexpected_job_exit_becomes_failed(self):
        process=subprocess.Popen([sys.executable,'-c','pass'],stdout=subprocess.PIPE,text=True)
        job={'id':'test','project':self.p.name,'step':'3','status':'running','log':[],'result':None}
        self.studio.observe_job(job,process);self.assertEqual(job['status'],'failed')
    def test_partial_regeneration_does_not_clear_other_stale_outputs(self):
        state=self.studio.state(self.p);state['stale']['3']={'reason':'Script edit','segments':[1,2]};self.studio.persist(self.p,state)
        process=subprocess.Popen([sys.executable,'-c','print(\'STUDIO_RESULT:{"success":true}\')'],stdout=subprocess.PIPE,text=True)
        job={'id':'test','project':self.p.name,'step':'3','status':'running','log':[],'result':None,'scope':[1],'opening':False}
        self.studio.observe_job(job,process)
        self.assertEqual(self.studio.state(self.p)['stale']['3']['segments'],[2])
    def test_failed_job_that_changed_nothing_keeps_the_step_current(self):
        self.studio.detail(self.p.name);state=self.studio.state(self.p);state['stale']={};self.studio.persist(self.p,state)
        process=subprocess.Popen([sys.executable,'-c','print(\'STUDIO_RESULT:{"success":false}\')'],stdout=subprocess.PIPE,text=True)
        job={'id':'test','project':self.p.name,'step':'3','status':'running','log':[],'result':None,'scope':None,'opening':True}
        self.studio.observe_job(job,process)
        self.assertEqual(job['status'],'failed');self.assertEqual(self.studio.detail(self.p.name)['state']['stale'],{})
    def test_opening_only_recording_outdates_only_the_opening_downstream(self):
        self.studio.detail(self.p.name);state=self.studio.state(self.p);state['stale']={};self.studio.persist(self.p,state)
        recording(self.p/'voice/opening.wav');(self.p/'voice/字幕.srt').write_text('1\n')
        process=subprocess.Popen([sys.executable,'-c','print(\'STUDIO_RESULT:{"success":true}\')'],stdout=subprocess.PIPE,text=True)
        job={'id':'test','project':self.p.name,'step':'3','status':'running','log':[],'result':None,'scope':[],'opening':True}
        self.studio.observe_job(job,process)
        stale=self.studio.detail(self.p.name)['state']['stale']
        self.assertNotIn('3',stale);self.assertEqual(stale['4']['segments'],[0])
    def test_subtitle_file_belongs_to_no_segment(self):
        self.studio.detail(self.p.name);state=self.studio.state(self.p);state['stale']={};self.studio.persist(self.p,state)
        (self.p/'voice/字幕.srt').write_text('changed\n')
        self.assertEqual(self.studio.detail(self.p.name)['state']['stale'],{})
    def test_pipeline_managed_rewrite_does_not_invalidate_its_own_step(self):
        self.studio.detail(self.p.name)
        raw=copy.deepcopy(RAW);raw['created_time']=None;atomic_json(self.p/'text/raw.json',raw)
        process=subprocess.Popen([sys.executable,'-c','print(\'STUDIO_RESULT:{"success":true}\')'],stdout=subprocess.PIPE,text=True)
        job={'id':'test','project':self.p.name,'step':'1.5','status':'running','log':[],'result':None}
        self.studio.observe_job(job,process)
        self.assertNotIn('1.5',self.studio.detail(self.p.name)['state']['stale'])
    def test_noop_writing_session_keeps_downstream_current(self):
        state=self.studio.state(self.p);state['agent_active']={'step':'1','indices':[],'raw_before':fingerprint(self.p/'text/raw.json')};self.studio.persist(self.p,state)
        result=self.studio.finish_agent(self.p.name,{})
        self.assertNotIn('3',result['state']['stale'])
    def test_assembly_rejects_outdated_inputs(self):
        state=self.studio.state(self.p);state['stale']['3']={'reason':'Edit','segments':[1]};self.studio.persist(self.p,state)
        with self.assertRaises(Conflict):self.studio.run_job(self.p.name,{'step':'5'})
    def batch_request(self, batch):
        from unittest.mock import patch
        with patch('server.subprocess.Popen') as spawn, patch('server.threading.Thread'):
            result = self.studio.run_job(self.p.name, {'step': '4', 'mode': 'static', 'batch': batch})
            request = json.loads(spawn.call_args.args[0][-1])
        self.studio.jobs.clear()
        return result, request
    def test_batch_missing_and_all_skip_motion_and_preserve_existing_images(self):
        script = copy.deepcopy(SCRIPT)
        script['segments'].append({'index':3,'content':'Motion','visualizer':'hf'})
        script['actual_segments'] = 3
        atomic_json(self.p/'text/script.json',script)
        (self.p/'images/segment_2.png').unlink()
        (self.p/'images/segment_3.mp4').write_bytes(b'motion')
        before = (self.p/'images/segment_1.png').read_bytes()
        job, request = self.batch_request('missing')
        self.assertEqual(request['indices'], [2]); self.assertFalse(request['opening'])
        self.assertEqual(job['scope'], [2])
        self.assertEqual((self.p/'images/segment_1.png').read_bytes(), before)
        _, request = self.batch_request('all')
        self.assertEqual(request['indices'], [1,2]); self.assertFalse(request['opening'])
    def test_batch_opening_only_and_nothing_missing(self):
        with self.assertRaisesRegex(ValueError, 'already available'):
            self.batch_request('missing')
        state = self.studio.state(self.p)
        state['settings'] = {'global': {'opening_quote': True}}
        self.studio.persist(self.p, state)
        _, request = self.batch_request('missing')
        self.assertEqual(request['indices'], []); self.assertTrue(request['opening'])
    def test_batch_requires_current_segments(self):
        state = self.studio.state(self.p)
        state['stale']['1.5'] = {'reason':'Changed manuscript','segments':[]}
        self.studio.persist(self.p,state)
        with self.assertRaisesRegex(Conflict, 'current segments'):
            self.batch_request('all')
    def test_visual_jobs_create_a_missing_or_outdated_direction_first(self):
        self.assertFalse(self.batch_request('all')[1]['summary'])
        state = self.studio.state(self.p); state['summary_stale'] = True; self.studio.persist(self.p, state)
        self.assertTrue(self.batch_request('all')[1]['summary'])
        state.pop('summary_stale'); self.studio.persist(self.p, state); (self.p/'text/mini_summary.json').unlink()
        job, request = self.batch_request('all')
        self.assertTrue(request['summary']); self.assertTrue(job['summary'])
    def test_manuscript_edit_outdates_the_direction_and_a_new_direction_outdates_only_visuals(self):
        d = self.studio.detail(self.p.name); value = copy.deepcopy(RAW); value['content'] = 'GUI 新版本。'
        after = self.studio.save(self.p.name, {'kind':'raw','value':value,'expected_hash':d['hashes']['raw']})
        self.assertTrue(after['state']['summary_stale']); self.assertNotIn('2', after['ready'])
        self.studio.persist(self.p, {**self.studio.state(self.p), 'stale': {}})
        after = self.studio.save(self.p.name, {'kind':'summary','value':{'summary':'新的画面基调'},'expected_hash':after['hashes']['summary']})
        self.assertNotIn('summary_stale', after['state'])
        self.assertEqual(sorted(after['state']['stale']), ['4','5']); self.assertEqual(after['state']['stale']['4']['reason'], 'Visual direction changed')
        restored = self.studio.restore(self.p.name, {'revision': after['state']['events'][0]['id']})
        self.assertEqual(restored['summary']['summary'], '上下文'); self.assertEqual(sorted(restored['state']['stale']), ['4','5'])
    def test_direction_job_outdates_visuals_and_clears_its_marker(self):
        self.studio.detail(self.p.name)
        state = self.studio.state(self.p); state['summary_stale'] = True; self.studio.persist(self.p, state)
        before = fingerprint(self.p/'text/mini_summary.json'); atomic_json(self.p/'text/mini_summary.json', {'summary':'重新生成的基调'})
        process = subprocess.Popen([sys.executable,'-c','print(\'STUDIO_RESULT:{"success":true}\')'],stdout=subprocess.PIPE,text=True)
        job = {'id':'direction','project':self.p.name,'step':'4','status':'running','log':[],'result':None,'scope':None,'mode':'summary','opening':False,'summary':True,'summary_before':before}
        self.studio.observe_job(job, process)
        after = self.studio.detail(self.p.name)
        self.assertNotIn('summary_stale', after['state']); self.assertEqual(sorted(after['state']['stale']), ['4','5'])
    def test_legacy_context_markers_move_to_the_direction(self):
        state = self.studio.state(self.p); state['stale']['2'] = {'reason':'Step 1.5 changed','segments':[]}; state['reviewed']['2'] = 'reviewed'
        self.studio.persist(self.p, state)
        after = self.studio.detail(self.p.name)
        self.assertTrue(after['state']['summary_stale']); self.assertNotIn('2', after['state']['stale']); self.assertNotIn('2', after['state']['reviewed'])
    def test_motion_agent_requires_a_current_direction(self):
        state = self.studio.state(self.p); state['summary_stale'] = True; self.studio.persist(self.p, state)
        with self.assertRaisesRegex(Conflict, 'visual direction'):
            self.studio.prepare_agent(self.p.name, {'step':'4','indices':[1]})
    def test_completed_static_batch_keeps_motion_outdated(self):
        script = copy.deepcopy(SCRIPT)
        script['segments'].append({'index':3,'content':'Motion','visualizer':'hf'})
        atomic_json(self.p/'text/script.json',script)
        self.studio.detail(self.p.name)
        state = self.studio.state(self.p)
        state['settings'] = {'global': {}}
        state['stale']['4'] = {'reason':'Edited style','segments':[]}
        self.studio.persist(self.p,state)
        process = subprocess.Popen([sys.executable,'-c','print(\'STUDIO_RESULT:{"success":true}\')'],stdout=subprocess.PIPE,text=True)
        job = {'id':'batch','project':self.p.name,'step':'4','status':'running','log':[],'result':None,'scope':[1,2],'mode':'static','opening':False}
        self.studio.observe_job(job,process)
        self.assertEqual(self.studio.state(self.p)['stale']['4']['segments'],[3])
    def test_visual_type_edit_preserves_recording_currency(self):
        d=self.studio.detail(self.p.name);value=copy.deepcopy(SCRIPT);value['segments'][0]['visualizer']='hf'
        after=self.studio.save(self.p.name,{'kind':'script','value':value,'expected_hash':d['hashes']['script'],'segments':[1]})
        self.assertNotIn('3',after['state']['stale']);self.assertIn('4',after['state']['stale']);self.assertIn('5',after['state']['stale'])
    def test_scoped_media_revision_preserves_unselected_outputs(self):
        paths=list((self.p/'images').rglob('*'))+list((self.p/'voice').rglob('*'))
        chosen=self.studio.scoped_media(paths,self.p,[1])
        self.assertTrue(chosen);self.assertTrue(all('_1.' in p.name for p in chosen));self.assertFalse(any('_2.' in p.name for p in chosen))
    def test_covers_reject_outdated_metadata(self):
        state=self.studio.state(self.p);state['stale']['1.5']={'reason':'Title edit','segments':[]};self.studio.persist(self.p,state)
        with self.assertRaises(Conflict):self.studio.run_job(self.p.name,{'step':'6'})
    def test_media_auth_and_byte_ranges(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.studio=self.studio;server.token='test-token'
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
            conn.request('GET','/api/projects');self.assertEqual(conn.getresponse().status,401)
            from urllib.parse import urlencode
            route='/api/media?'+urlencode({'project':self.p.name,'path':'voice/voice_1.wav','token':'test-token'})
            conn.request('GET',route,headers={'Range':'bytes=0-3'});response=conn.getresponse()
            self.assertEqual(response.status,206);self.assertEqual(response.read(),b'RIFF')
            conn.request('GET',route,headers={'Range':'bytes=999999-'});self.assertEqual(conn.getresponse().status,416)
            conn.close()
        finally:server.shutdown();server.server_close()

    def test_unchanged_script_save_keeps_later_steps_current(self):
        detail=self.studio.detail(self.p.name);state=self.studio.state(self.p);state['stale']={};self.studio.persist(self.p,state)
        events=len(state['events'])
        after=self.studio.save(self.p.name,{'kind':'script','value':detail['script'],'expected_hash':detail['hashes']['script'],'segments':[1]})
        self.assertEqual(after['state']['stale'],{});self.assertEqual(len(after['state']['events']),events)

    def test_settings_change_marks_only_the_affected_step(self):
        state=self.studio.state(self.p);state['settings']={'step1':{'llm_model':'chosen-model'},'step4':{'image_size':'1280x720'}};state['stale']={}
        self.studio.persist(self.p,state)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.studio=self.studio;server.token='test-token'
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
            body={'settings':{'step1':{'llm_model':'chosen-model'},'step4':{'image_server':'google_adc'}}}
            route='/api/projects/'+__import__('urllib.parse',fromlist=['quote']).quote(self.p.name)+'/settings'
            conn.request('POST',route,body=json.dumps(body),headers={'Authorization':'Bearer test-token','Content-Type':'application/json'})
            self.assertEqual(conn.getresponse().status,200);conn.close()
        finally:server.shutdown();server.server_close()
        state=self.studio.state(self.p)
        self.assertEqual(state['settings']['step4'],{'image_size':'1280x720','image_server':'google_adc'})
        self.assertIn('4',state['stale']);self.assertNotIn('1',state['stale']);self.assertNotIn('1.5',state['stale'])

    def test_saved_keys_override_environment_without_entering_project_data(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-environment-fallback'}), patch('server.subprocess.Popen') as spawn, patch('server.threading.Thread'):
            self.studio.set_credentials({'DEEPSEEK_API_KEY': 'test-saved-secret'})
            result = self.studio.run_job(self.p.name, {'step': '3', 'indices': [1]})
            first_environment = spawn.call_args.kwargs['env']
            self.assertEqual(first_environment['DEEPSEEK_API_KEY'], 'test-saved-secret')
            self.assertNotIn('test-saved-secret', json.dumps(result))
            self.assertNotIn('test-saved-secret', json.dumps(spawn.call_args.args))
            self.assertNotIn('test-saved-secret', json.dumps(self.studio.detail(self.p.name)))
            for path in (self.p / '.studio').rglob('*.json'):
                self.assertNotIn('test-saved-secret', path.read_text())
            self.studio.set_credentials({})
            self.studio.jobs.clear()
            self.studio.run_job(self.p.name, {'step': '3'})
            self.assertEqual(spawn.call_args.kwargs['env']['DEEPSEEK_API_KEY'], 'test-environment-fallback')
            self.assertEqual(first_environment['DEEPSEEK_API_KEY'], 'test-saved-secret')

    def test_credential_updates_reject_unknown_variables_and_invalid_values(self):
        for keys in [{'PATH': 'anything'}, {'DEEPSEEK_API_KEY': ''}, {'DEEPSEEK_API_KEY': 'bad\nvalue'}, {'DEEPSEEK_API_KEY': 1}, []]:
            with self.assertRaises(ValueError): self.studio.set_credentials(keys)
        self.assertEqual(self.studio.credentials, {})

    def test_credential_transfer_is_authenticated_native_only_and_not_readable(self):
        from unittest.mock import patch
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.studio = self.studio; server.token = 'test-token'
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
            payload = json.dumps({'DEEPSEEK_API_KEY': 'test-saved-secret'})
            headers = {'Authorization': 'Bearer test-token', 'Content-Type': 'application/json'}
            conn.request('POST', '/api/credentials', payload)
            response = conn.getresponse(); self.assertEqual(response.status, 401); response.read()
            with patch.dict(os.environ, {'STUDIO_NATIVE_HOST': '0'}):
                conn.request('POST', '/api/credentials', payload, headers)
                response = conn.getresponse(); self.assertEqual(response.status, 400); response.read()
            with patch.dict(os.environ, {'STUDIO_NATIVE_HOST': '1'}):
                conn.request('POST', '/api/credentials', payload, headers)
                response = conn.getresponse(); self.assertEqual(response.status, 200)
                self.assertNotIn(b'test-saved-secret', response.read())
            self.assertEqual(self.studio.credentials['DEEPSEEK_API_KEY'], 'test-saved-secret')
            conn.request('GET', '/api/credentials', headers=headers)
            response = conn.getresponse(); self.assertEqual(response.status, 404); self.assertNotIn(b'test-saved-secret', response.read())
            conn.close()
        finally: server.shutdown(); server.server_close()

    def test_worker_logs_redact_saved_keys(self):
        process = subprocess.Popen([sys.executable, '-c', "print('Provider rejected test-saved-secret')"], stdout=subprocess.PIPE, text=True)
        job = {'id': 'test', 'project': self.p.name, 'step': '3', 'status': 'running', 'log': [], 'result': None, '_secrets': ['test-saved-secret']}
        self.studio.observe_job(job, process)
        self.assertEqual(job['log'], ['Provider rejected [hidden API key]'])
        self.assertNotIn('test-saved-secret', json.dumps(self.studio.public_job(job)))

class PipelineTests(unittest.TestCase):
    def setUp(self):
        if not ENGINE.exists():self.skipTest('Sibling CLI engine is not installed')
        self.temp=tempfile.TemporaryDirectory();self.p=Path(self.temp.name)/'GUI 验证 with spaces'
        for folder in ['text','images','voice','.studio']:(self.p/folder).mkdir(parents=True)
        atomic_json(self.p/'text/raw.json',copy.deepcopy(RAW))
    def tearDown(self):
        if hasattr(self,'temp'):self.temp.cleanup()
    def run_worker(self,step,settings):
        request={'engine':str(ENGINE),'project':str(self.p),'step':step,'settings':settings}
        result=subprocess.run([sys.executable,'-u',str(DESKTOP/'backend/runner.py'),json.dumps(request,ensure_ascii=False)],capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout[-4000:]+result.stderr[-2000:])
        return result
    def test_gui_manuscript_overrides_stale_docx_before_segmentation(self):
        sys.path.insert(0,str(ENGINE));from core.domain.docx_transform import export_raw_to_docx
        old=copy.deepcopy(RAW);old['content']='旧的 Word 内容。';export_raw_to_docx(old,str(self.p/'text/raw.docx'))
        self.run_worker('1.5',{'step1':{'writing_mode':'direct'},'step1_5':{'num_segments':2,'split_mode':'manual'}})
        text=''.join(s['content'] for s in json.loads((self.p/'text/script.json').read_text())['segments'])
        self.assertIn('GUI',text);self.assertNotIn('旧的 Word',text)
    def test_selected_boxes_reach_script_cover_and_opening_inputs(self):
        # Exercise the real worker and its DOCX round trip, without calling a provider.
        studio = object.__new__(Studio)
        studio.engine = ENGINE; studio.workspace = self.p.parent.resolve(); studio.data = Path(self.temp.name)/'data'
        studio.lock = threading.RLock(); studio.jobs = {}; studio.credentials = {}
        detail = studio.detail(self.p.name)
        value = copy.deepcopy(RAW)
        value.update(video_titles=['原视频标题', '选择的视频标题', '备选视频标题'],
                     cover_titles=['原封面标题', '备选封面标题', '选择的封面标题'],
                     cover_subtitles=['原封面副标题', '选择的封面副标题'],
                     golden_quotes=['原开场金句', '选择的开场金句。\n这是同一个候选项的第二行。'])
        studio.save(self.p.name, {'kind': 'raw', 'value': value,
                    'selections': {'video_titles': 1, 'cover_titles': 2, 'cover_subtitles': 1, 'golden_quotes': 1},
                    'expected_hash': detail['hashes']['raw']})
        sys.path.insert(0, str(ENGINE))
        from core.domain.docx_transform import export_raw_to_docx
        export_raw_to_docx(RAW, str(self.p/'text/raw.docx'))
        self.run_worker('1.5', {'step1_5': {'num_segments': 2, 'split_mode': 'manual'}})
        script = json.loads((self.p/'text/script.json').read_text())
        raw = json.loads((self.p/'text/raw.json').read_text())
        from core.domain.metadata import get_primary_video_title, get_primary_cover_title, get_primary_cover_subtitle, get_primary_golden_quote
        from core.infra.media.three_by_four import select_cover_copy
        self.assertEqual(get_primary_video_title(script), value['video_titles'][1])
        self.assertEqual(get_primary_cover_title(script), value['cover_titles'][2])
        self.assertEqual(get_primary_cover_subtitle(script), value['cover_subtitles'][1])
        self.assertEqual(get_primary_golden_quote(script), value['golden_quotes'][1])
        self.assertEqual(select_cover_copy(script), (value['cover_titles'][2], value['cover_subtitles'][1]))
        for field in ['video_titles', 'cover_titles', 'cover_subtitles', 'golden_quotes']:
            self.assertCountEqual(script[field], value[field])
            self.assertEqual(raw[field], script[field])
    def test_real_offline_assembly_produces_both_deliverables(self):
        from PIL import Image
        from moviepy import VideoFileClip
        script = {**copy.deepcopy(SCRIPT), 'golden_quotes': RAW['golden_quotes']}
        atomic_json(self.p/'text/script.json',script)
        for i in [1,2]:
            Image.new('RGB',(320,180),(30*i,80,100)).save(self.p/f'images/segment_{i}.png');recording(self.p/f'voice/voice_{i}.wav')
        Image.new('RGB',(320,180),(210,45,30)).save(self.p/'images/opening.png'); recording(self.p/'voice/opening.wav')
        for enabled in [True, False]:
            with self.subTest(opening=enabled):
                self.run_worker('5',{'global':{'opening_quote':enabled},'step5':{'video_size':'320x180','video_output_fps':12,'video_codec':'h264','enable_subtitles':False,'bgm_filename':None,'narration_speed_factor':1.0,'image_zoom_in_ratio':1.0,'ending_fade_seconds':0.0}})
                for name in ['final_video.mp4','final_video_3x4.mp4']:
                    target=self.p/name;self.assertGreater(target.stat().st_size,1000);self.assertAlmostEqual(duration(target),3.0 if enabled else 2.0,delta=.5)
                with VideoFileClip(str(self.p/'final_video.mp4')) as video:
                    red, green, blue = video.get_frame(.2)[20,20]
                    self.assertEqual(bool(red > blue), enabled)
                self.assertTrue((self.p/'voice/opening.wav').exists())
                self.assertTrue((self.p/'images/opening.png').exists())


class AgentContextTests(unittest.TestCase):
    setUp=PipelineTests.setUp
    tearDown=PipelineTests.tearDown
    def test_saved_key_reaches_both_agent_provider_environments(self):
        atomic_json(self.p/'.studio/state.json', {'settings': {'step1': {'writing_mode': 'direct', 'llm_server': 'deepseek', 'llm_model': 'deepseek-chat'}, 'step4': {'llm_server': 'deepseek', 'llm_model': 'deepseek-chat'}}})
        probe = "from agent_env import resolve; from pathlib import Path; import sys; value=resolve(Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]); assert value['ANTHROPIC_API_KEY']=='test-saved-secret'; assert value['ANTHROPIC_AUTH_TOKEN']=='test-saved-secret'"
        for step in ['1', '4']:
            result = subprocess.run([sys.executable, '-c', probe, str(ENGINE), str(self.p), step], cwd=DESKTOP/'backend', env={**os.environ, 'DEEPSEEK_API_KEY': 'test-saved-secret'}, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, 'Agent environment did not use the saved provider key')
    def test_real_stage_context_contracts(self):
        from server import Studio
        from context import prepare
        studio=object.__new__(Studio);studio.engine=studio.library_root=ENGINE
        source=self.p/'source.txt';source.write_text('Verification source')
        atomic_json(self.p/'.studio/state.json',{'settings':{},'source':str(source),'stale':{},'reviewed':{},'events':[]})
        atomic_json(self.p/'text/script.json',copy.deepcopy(SCRIPT));atomic_json(self.p/'text/mini_summary.json',{'summary':'Shared context'})
        recording(self.p/'voice/voice_1.wav')
        for step in ['1','4']:
            launch=prepare(studio,self.p,{'step':step,'indices':[1]})
            context=Path(launch['context']).read_text()
            self.assertIn('--append-system-prompt-file',launch['args']);self.assertNotIn('{output_json}',context)
            if step=='4':self.assertIn('"durationSeconds": 1.0',context);self.assertIn('Shared context',context)
    def test_direct_context_excludes_previous_source_and_keeps_output_contract(self):
        from context import prepare
        studio = object.__new__(Studio); studio.engine = studio.library_root = ENGINE
        for source in ['', str(self.p/'missing-old-manuscript.txt')]:
            with self.subTest(source=source):
                atomic_json(self.p/'.studio/state.json', {'settings': {'step1': {'writing_mode': 'direct'}}, 'source': source})
                launch = prepare(studio, self.p, {'step': '1'})
                prompt = Path(launch['context']).read_text()
                self.assertIn('DIRECT WRITING MODE', prompt)
                self.assertIn('Wait for the user', prompt)
                self.assertIn(str(self.p/'text/raw.json'), prompt)
                self.assertIn(str(ENGINE/'skills/step1'), prompt)
                for field in RAW:
                    self.assertIn(field, prompt)
                self.assertNotIn('missing-old-manuscript.txt', prompt)
                self.assertNotIn(str(ENGINE/'input'), launch['args'])
                self.assertNotIn('{input_file}', prompt)
    def test_source_context_requires_source_and_grants_its_folder(self):
        from context import prepare
        studio = object.__new__(Studio); studio.engine = studio.library_root = ENGINE
        atomic_json(self.p/'.studio/state.json', {'settings': {'step1': {'writing_mode': 'source'}}, 'source': ''})
        with self.assertRaisesRegex(ValueError, 'top of Writing'):
            prepare(studio, self.p, {'step': '1'})
        source = self.p/'资料 folder'/'input.txt'; source.parent.mkdir(); source.write_text('input')
        atomic_json(self.p/'.studio/state.json', {'settings': {}, 'source': str(source)})
        launch = prepare(studio, self.p, {'step': '1'})
        self.assertIn(str(source), Path(launch['context']).read_text())
        self.assertIn(str(source.parent), launch['args'])
        self.assertNotIn('DIRECT WRITING MODE', Path(launch['context']).read_text())

class MotionRenderTests(unittest.TestCase):
    setUp=PipelineTests.setUp
    tearDown=PipelineTests.tearDown
    def test_saved_composition_is_rendered_without_agent_rewrite(self):
        from unittest.mock import patch
        import runner
        script=copy.deepcopy(SCRIPT);script['segments'][0]['visualizer']='hf';atomic_json(self.p/'text/script.json',script)
        work=self.p/'images/hyperframes/segment_1';work.mkdir(parents=True)
        html='<!DOCTYPE html><p>Existing user composition</p>';(work/'index.html').write_text(html)
        atomic_json(work/'studio-payload.json',{'durationSeconds':99,'content':'Old narration'})
        recording(self.p/'voice/voice_1.wav');(self.p/'images/segment_1.png').write_bytes(b'previous image')
        calls=[]
        def render(args,**kwargs):
            calls.append(args);Path(args[args.index('--output')+1]).write_bytes(b'rendered media')
            return subprocess.CompletedProcess(args,0)
        original=Path.cwd()
        try:
            with patch('runner.subprocess.run',side_effect=render),patch('server.duration',return_value=1.0):
                result=runner.run({'engine':str(ENGINE),'project':str(self.p),'step':'4','mode':'render','indices':[1],'settings':{}})
        finally:os.chdir(original)
        from core.infra.hyperframes.runtime import hyperframes_command
        self.assertTrue(result['success']);self.assertEqual((work/'index.html').read_text(),html)
        self.assertEqual(len(calls),1);self.assertEqual(calls[0][:len(hyperframes_command())+1],[*hyperframes_command(),'render'])
        payload=json.loads(calls[0][calls[0].index('--variables')+1]);self.assertEqual(payload['durationSeconds'],1.0);self.assertEqual(payload['content'],script['segments'][0]['content'])
        self.assertEqual((self.p/'images/segment_1.mp4').read_bytes(),b'rendered media');self.assertFalse((self.p/'images/segment_1.png').exists())
        self.assertEqual((self.p/'images/stills/segment_1.png').read_bytes(),b'previous image')

class ImageModelTests(unittest.TestCase):
    """A model left over from another image service is refused before any provider is called."""
    setUp=PipelineTests.setUp
    tearDown=PipelineTests.tearDown
    def run_job(self,step,section,settings):
        from unittest.mock import patch
        import runner
        atomic_json(self.p/'text/script.json',copy.deepcopy(SCRIPT))
        original=Path.cwd()
        try:
            with patch('core.infra.ai.llm_client.text_to_image_google') as google:
                with self.assertRaisesRegex(ValueError,'doubao-seedream-4-0-250828 does not work with google_adc'):
                    runner.run({'engine':str(ENGINE),'project':str(self.p),'step':step,'settings':{section:settings}})
                google.assert_not_called()
        finally:os.chdir(original)
    def test_segment_images(self):
        self.run_job('4','step4',{'image_server':'google_adc','image_model':'doubao-seedream-4-0-250828'})
    def test_covers(self):
        self.run_job('6','step6',{'cover_image_server':'google_adc','cover_image_model':'doubao-seedream-4-0-250828'})

class GoogleLoginCheckTests(unittest.TestCase):
    """The Settings check of the Google Cloud login keeps its last result across restarts."""
    setUp=ProjectTests.setUp
    open=ProjectTests.open
    tearDown=ProjectTests.tearDown
    def test_result_and_time_are_kept(self):
        from unittest.mock import patch
        self.assertEqual(self.studio.google_adc_status(),{})
        output=json.dumps({'ok':True,'project':'p','location':'global','models':[{'model':'gemini-3.1-flash-image','ok':True}]})
        with patch('server.subprocess.run',return_value=subprocess.CompletedProcess([],0,output+'\n','')) as run:
            result=self.studio.check_google_adc({'models':['gemini-3.1-flash-image','gemini-3.1-flash-image',' ']})
        self.assertEqual(json.loads(run.call_args.args[0][-1])['models'],['gemini-3.1-flash-image'])
        self.assertTrue(result['ok']);self.assertIn('T',result['checked'])
        self.assertEqual(self.open().google_adc_status(),result)
    def test_a_stalled_check_is_reported(self):
        from unittest.mock import patch
        with patch('server.subprocess.run',side_effect=subprocess.TimeoutExpired('google_check',90)):
            result=self.studio.check_google_adc({})
        self.assertFalse(result['ok']);self.assertIn('90 seconds',result['message']);self.assertIn('checked',result)

class VoiceJobTests(unittest.TestCase):
    """The voice job gets the TTS service, and a project saved with an empty voice uses the default one."""
    setUp=PipelineTests.setUp
    tearDown=PipelineTests.tearDown
    def test_service_and_default_voice(self):
        from unittest.mock import patch
        import runner
        atomic_json(self.p/'text/script.json',copy.deepcopy(SCRIPT))
        import functools
        sys.path.insert(0,str(ENGINE));from core.pipeline import steps
        captured={}
        @functools.wraps(steps.run_step_3)  # the runner passes what the real signature accepts
        def fake(**kwargs):
            captured.update(kwargs);return {'success':True}
        original=Path.cwd()
        try:
            with patch.object(steps,'run_step_3',fake):
                runner.run({'engine':str(ENGINE),'project':str(self.p),'step':'3','settings':{'step3':{'voice':''}}})
        finally:os.chdir(original)
        self.assertEqual(captured['tts_server'],'bytedance')
        self.assertTrue(captured['voice'])

class MotionContextTests(unittest.TestCase):
    """A session for several motion segments lists every target; a missing composition is reported when it ends."""
    def setUp(self):
        if not ENGINE.exists():self.skipTest('Sibling CLI engine is not installed')
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.studio=Studio(ENGINE,root/'data',root/'workspace')
        created=self.studio.create({'name':'Motion','source':'','settings':{'step1':{'writing_mode':'direct'}}})
        self.id,self.p=created['id'],Path(created['path'])
        script={'segments':[{'index':i,'content':f'第{i}段','visualizer':'hf'} for i in (1,2,3)]}
        atomic_json(self.p/'text/script.json',script);atomic_json(self.p/'text/mini_summary.json',{'summary':'基调'})
        for i in (1,2,3):recording(self.p/f'voice/voice_{i}.wav')
        self.studio.detail(self.id)
    def tearDown(self):
        if hasattr(self,'temp'):self.temp.cleanup()
    def test_task_list_and_missing_composition_warning(self):
        launch=self.studio.prepare_agent(self.id,{'step':'4','indices':[1,3]})
        context=Path(launch['context']).read_text()
        for i in (1,3):
            self.assertIn(f"Segment {i} → `{self.p/'images/hyperframes'/f'segment_{i}'/'index.html'}`",context)
            self.assertIn(f'# Segment {i} (task',context)
        self.assertNotIn('segment_2/index.html',context)
        self.assertEqual(launch['cwd'],str(self.p/'images/hyperframes'))
        (self.p/'images/hyperframes/segment_3/index.html').write_text('<!DOCTYPE html>')
        finished=self.studio.finish_agent(self.id,{})
        self.assertIn('segment 1.',finished['warning']);self.assertNotIn('3',finished['warning'])
    def test_single_segment_keeps_its_own_instructions(self):
        launch=self.studio.prepare_agent(self.id,{'step':'4','indices':[2]})
        context=Path(launch['context']).read_text()
        self.assertTrue(context.startswith('The selected segment is your task.'));self.assertNotIn('task 1 of',context)
        work=self.p/'images/hyperframes/segment_2'
        self.assertEqual(launch['cwd'],str(work));self.assertIn(f'cd "{work}"',context);self.assertIn(str(work/'snapshots'),context)
        self.assertIn('Finish & render',context)
        self.studio.finish_agent(self.id,{})

class SegmentVisualTests(unittest.TestCase):
    """A segment switches between a still and motion without losing either; motion reports each stage."""
    setUp=ProjectTests.setUp
    open=ProjectTests.open
    tearDown=ProjectTests.tearDown
    def switch(self,visualizer,index=1):
        d=self.studio.detail(self.p.name)
        return self.studio.set_visual(self.p.name,{'index':index,'visualizer':visualizer,'expected_hash':d['hashes']['script']})
    def compose(self,index=1,frames=2):
        work=self.p/'images/hyperframes'/f'segment_{index}';(work/'snapshots').mkdir(parents=True,exist_ok=True)
        (work/'index.html').write_text('<!DOCTYPE html>')
        for n in range(frames):(work/'snapshots'/f'frame-0{n}-at-{n}.0s.png').write_bytes(b'frame')
        os.utime(work/'index.html',(1000,1000))
        return work
    def test_motion_without_a_render_shows_no_video_but_keeps_the_still(self):
        self.compose()
        after=self.switch('hf');segment=after['segments'][0]
        self.assertIsNone(segment['visual']);self.assertEqual(segment['still'],'images/segment_1.png')
        self.assertEqual(segment['motion']['composition'],'images/hyperframes/segment_1/index.html')
        self.assertEqual(len(segment['motion']['keyframes']),2);self.assertTrue(segment['motion']['keyframes_current'])
        self.assertIsNone(segment['motion']['video']);self.assertIn('snapshots/frame-01',segment['poster'])
        self.assertEqual(after['state']['stale']['4']['segments'],[1]);self.assertFalse(after['ready']['4'])
        self.assertTrue((self.p/'images/segment_1.png').exists())
    def test_rendered_motion_and_still_trade_places(self):
        self.compose();(self.p/'images/segment_1.mp4').write_bytes(b'video')
        after=self.switch('hf')
        self.assertEqual(after['segments'][0]['visual'],'images/segment_1.mp4');self.assertTrue(after['segments'][0]['motion']['video_current'])
        self.assertFalse((self.p/'images/segment_1.png').exists());self.assertEqual((self.p/'images/stills/segment_1.png').read_bytes(),b'visual')
        self.assertNotIn('4',after['state']['stale']);self.assertIn('5',after['state']['stale'])
        back=self.switch('image')
        self.assertEqual(back['segments'][0]['visual'],'images/segment_1.png');self.assertEqual((self.p/'images/segment_1.png').read_bytes(),b'visual')
        self.assertNotIn('4',back['state']['stale'])
        self.assertEqual(json.loads((self.p/'text/script.json').read_text())['segments'][0]['visualizer'],'image')
    def test_an_edited_composition_outdates_its_render(self):
        work=self.compose();(self.p/'images/segment_1.mp4').write_bytes(b'video');os.utime(self.p/'images/segment_1.mp4',(500,500))
        segment=self.switch('hf')['segments'][0]
        self.assertFalse(segment['motion']['video_current']);self.assertEqual(self.studio.detail(self.p.name)['state']['stale']['4']['segments'],[1])
        os.utime(work/'snapshots/frame-00-at-0.0s.png',(10,10))
        self.assertFalse(self.studio.detail(self.p.name)['segments'][0]['motion']['keyframes_current'])
    def test_switch_is_refused_during_an_agent_or_after_an_outside_edit(self):
        with self.assertRaises(Conflict):
            self.studio.set_visual(self.p.name,{'index':1,'visualizer':'hf','expected_hash':'old'})
        state=self.studio.state(self.p);state['agent_active']={'step':'4','indices':[2]};self.studio.persist(self.p,state)
        with self.assertRaises(Conflict):self.switch('hf')
    def test_ending_a_motion_session_keeps_other_outdated_segments(self):
        state=self.studio.state(self.p);state['stale']['4']={'reason':'Visual type changed','segments':[2]}
        state['agent_active']={'step':'4','indices':[1],'launch':'x','owner':0,'raw_before':''};self.studio.persist(self.p,state)
        script=json.loads((self.p/'text/script.json').read_text());script['segments'][0]['visualizer']='hf';atomic_json(self.p/'text/script.json',script)
        self.compose()
        finished=self.studio.finish_agent(self.p.name,{})
        self.assertEqual(finished['finished'],{'step':'4','indices':[1]});self.assertIsNone(finished['warning'])
        self.assertEqual(finished['state']['stale']['4']['segments'],[1,2])

    def test_an_interrupted_session_keeps_other_outdated_segments(self):
        state=self.studio.state(self.p);state['stale']['4']={'reason':'Visual type changed','segments':[2]}
        state['agent_active']={'step':'4','indices':[1],'launch':'x','owner':999999,'raw_before':''};self.studio.persist(self.p,state)
        reopened=self.open().state(self.p)
        self.assertNotIn('agent_active',reopened);self.assertEqual(reopened['stale']['4']['segments'],[1,2])

class WorkspaceTests(unittest.TestCase):
    """Projects live in the workspace folder or at any location picked at creation."""
    setUp=ProjectTests.setUp
    open=ProjectTests.open
    tearDown=ProjectTests.tearDown
    def elsewhere(self):
        folder=Path(self.temp.name)/'别处 elsewhere';folder.mkdir();return folder
    def test_default_location_is_the_workspace_outside_the_engine(self):
        location=Path(self.studio.bootstrap()['default_location'])
        self.assertEqual(location,(Path(self.temp.name)/'Book2Video Studio').resolve());self.assertFalse(location.is_relative_to(self.engine))
    def test_new_project_without_location_goes_in_the_workspace_with_a_clean_name(self):
        created=self.studio.create({'name':'自由写作'})
        self.assertEqual(created['id'],'自由写作');self.assertEqual(Path(created['path']),self.studio.workspace/'自由写作')
        self.assertEqual(self.studio.create({'name':'自由写作'})['id'],'自由写作_2')
    def test_new_project_at_a_chosen_location_is_linked_and_listed(self):
        place=self.elsewhere()
        created=self.studio.create({'name':'Remote book','location':str(place)})
        project=place/'Remote book'
        self.assertEqual(Path(created['path']),project.resolve());self.assertTrue((project/'text').is_dir())
        self.assertNotEqual(created['id'],'Remote book');self.assertEqual(self.studio.project(created['id']),project.resolve())
        self.assertEqual(self.studio.detail(created['id'])['path'],str(project.resolve()))
        listed={p['id']:p for p in self.studio.projects()}
        self.assertTrue(listed[created['id']]['linked']);self.assertFalse(listed[self.p.name]['linked'])
        # The link survives a restart of the service, and a repeat name does not reuse the folder.
        self.assertEqual(self.open().project(created['id']),project.resolve())
        self.assertEqual(Path(self.studio.create({'name':'Remote book','location':str(place)})['path']).name,'Remote book_2')
        self.assertEqual(self.studio.link(project),created['id'])
    def test_project_location_must_be_a_real_absolute_folder(self):
        for location in ['relative/folder',str(Path(self.temp.name)/'missing'),str(self.p/'text/raw.json')]:
            with self.subTest(location=location),self.assertRaises(ValueError):self.studio.create({'name':'x','location':location})
    def test_project_name_cannot_escape_its_location(self):
        place=self.elsewhere()
        for name in ['../escape','..','a/b','.']:
            created=self.studio.create({'name':name,'location':str(place)})
            self.assertEqual(Path(created['path']).parent,place.resolve())
    def test_restart_keeps_the_project_location(self):
        place=self.elsewhere()
        created=self.studio.create({'name':'Remote book','location':str(place)})
        restarted=self.studio.restart(created['id'],{})
        self.assertEqual(Path(restarted['path']).parent,place.resolve());self.assertNotEqual(restarted['id'],created['id'])
    def test_linked_projects_stay_confined_to_registered_folders(self):
        place=self.elsewhere();(place/'unregistered'/'text').mkdir(parents=True)
        for name in [str(place/'unregistered'),'ext-deadbeef-unregistered','../'+place.name]:
            with self.assertRaises(ValueError):self.studio.project(name)
        created=self.studio.create({'name':'Remote book','location':str(place)})
        import shutil;shutil.rmtree(created['path'])
        with self.assertRaises(ValueError):self.studio.project(created['id'])
        self.assertNotIn(created['id'],[p['id'] for p in self.studio.projects()])
    def test_project_id_round_trips_for_agent_launches(self):
        # Launch manifests name the project by id, so the id must resolve back to the folder.
        place=self.elsewhere();created=self.studio.create({'name':'Remote book','location':str(place)})
        for project_id in [created['id'],self.p.name]:
            self.assertEqual(self.studio.project_id(self.studio.project(project_id)),project_id)
        bare=object.__new__(Studio);self.assertEqual(bare.project_id(self.p),self.p.name)

    def test_bootstrap_reports_components_and_no_engine_input_folder(self):
        result=self.studio.bootstrap()
        self.assertNotIn('inputs',result);self.assertNotIn('engine',result)
        self.assertEqual(result['library_folder'],str(self.studio.library_root))
        python=next(c for c in result['components'] if c['name']=='Python')
        self.assertEqual(python['path'],sys.executable)
    def test_existing_project_folder_can_be_opened_and_listed(self):
        place=self.elsewhere();project=place/'旧项目';(project/'text').mkdir(parents=True)
        opened=self.studio.open_existing({'path':str(project)})
        self.assertEqual(self.studio.project(opened['id']),project.resolve())
        self.assertEqual(self.studio.open_existing({'path':str(project)})['id'],opened['id'])
        self.assertEqual(self.studio.open_existing({'path':str(self.p)})['id'],self.p.name)
        for path in ['',str(place),'relative/folder']:
            with self.subTest(path=path),self.assertRaises(ValueError):self.studio.open_existing({'path':path})
    def test_unreadable_project_does_not_stop_the_service(self):
        # For example, a linked project on the Desktop when folder access was not granted.
        place=self.elsewhere();created=self.studio.create({'name':'Private','location':str(place)})
        state=Path(created['path'])/'.studio/state.json';state.chmod(0)
        try:
            listed={p['id']:p for p in self.open().projects()}
            self.assertEqual(listed[created['id']]['problem'],'Cannot open this folder')
            self.assertNotIn('problem',listed[self.p.name])
        finally:state.chmod(0o644)
    def test_projects_from_the_cli_engine_build_are_listed_once(self):
        engine=self.elsewhere();output=engine/'output';(output/'书 A'/'text').mkdir(parents=True);(output/'notes').mkdir()
        external=Path(self.temp.name)/'外部'/'书 B';(external/'text').mkdir(parents=True)
        atomic_json(output/'.studio-projects.json',{'ext-1-书 B':str(external)})
        config=Path(self.temp.name)/'runtime.json';config.write_text(json.dumps({'engine':str(engine),'python':'python3'}))
        self.studio.adopt_legacy(config)
        paths={Path(p['path']).resolve() for p in self.studio.projects()}
        self.assertTrue({(output/'书 A').resolve(),external.resolve()}<=paths);self.assertNotIn((output/'notes').resolve(),paths)
        self.assertFalse(config.exists());self.assertTrue(config.with_name('runtime.json.migrated').exists())

class RemoveProjectTests(unittest.TestCase):
    """The project list can be cleaned up one by one or all at once, with or without the files."""
    setUp=ProjectTests.setUp
    open=ProjectTests.open
    tearDown=ProjectTests.tearDown
    def listed(self,studio=None):return [p['id'] for p in (studio or self.studio).projects()]
    def remote(self,name='Remote book'):
        place=Path(self.temp.name)/'别处';place.mkdir(exist_ok=True);return self.studio.create({'name':name,'location':str(place)})
    def test_removing_a_workspace_project_hides_it_and_keeps_its_files(self):
        result=self.studio.remove_projects({'ids':[self.p.name]})
        self.assertEqual(result['removed'],[self.p.name]);self.assertNotIn(self.p.name,self.listed())
        self.assertTrue((self.p/'text/raw.json').exists())
        self.assertNotIn(self.p.name,self.listed(self.open()))  # stays removed after a restart
    def test_removing_a_linked_project_drops_the_link_and_keeps_its_files(self):
        created=self.remote();self.studio.remove_projects({'ids':[created['id']]})
        self.assertNotIn(created['id'],self.listed());self.assertTrue((Path(created['path'])/'text').is_dir())
        with self.assertRaises(ValueError):self.studio.project(created['id'])
    def test_a_removed_project_comes_back_when_opened_again(self):
        self.studio.remove_projects({'ids':[self.p.name]})
        self.assertEqual(self.studio.open_existing({'path':str(self.p)})['id'],self.p.name);self.assertIn(self.p.name,self.listed())
    def test_deleting_files_removes_the_folder_and_does_not_hide_a_new_project_of_that_name(self):
        created=self.remote();other=self.studio.create({'name':'Other'})
        self.studio.remove_projects({'ids':[self.p.name,created['id']],'delete_files':True})
        self.assertFalse(self.p.exists());self.assertFalse(Path(created['path']).exists())
        self.assertEqual(self.listed(),[other['id']])
        self.assertEqual(self.studio.create({'name':self.p.name})['id'],self.p.name);self.assertIn(self.p.name,self.listed())
    def test_remove_all_clears_the_list(self):
        self.remote();self.studio.create({'name':'Other'})
        result=self.studio.remove_projects({'ids':self.listed()})
        self.assertEqual(result['projects'],[]);self.assertEqual(self.listed(),[])
    def test_busy_or_unknown_projects_are_refused_without_changing_anything(self):
        other=self.studio.create({'name':'Other'})
        self.studio.jobs['j']={'id':'j','project':self.p.name,'step':'3','status':'running','log':[],'result':None}
        with self.assertRaises(Conflict):self.studio.remove_projects({'ids':[other['id'],self.p.name],'delete_files':True})
        self.assertTrue((self.p/'text').is_dir());self.assertTrue(Path(other['path']).is_dir());self.assertIn(other['id'],self.listed())
        for ids in [[],'x',[1],['no-such-project'],[other['id'],'../escape']]:
            with self.subTest(ids=ids),self.assertRaises(ValueError):self.studio.remove_projects({'ids':ids})
        self.assertIn(other['id'],self.listed())
    def test_delete_files_only_applies_to_the_given_project_folders(self):
        outside=Path(self.temp.name)/'unrelated.txt';outside.write_text('keep')
        # A project folder holding the user data folder is never deleted, even if it is linked.
        data=self.studio.data;(data/'text').mkdir(exist_ok=True)
        self.studio.link(data)
        with self.assertRaises(ValueError):self.studio.remove_projects({'ids':[self.studio.project_id(data)],'delete_files':True})
        self.assertTrue(data.is_dir());self.assertTrue(outside.exists())
    def test_remove_endpoint_confirms_the_request_shape(self):
        # delete_files must be the boolean true; anything else only removes from the list.
        self.studio.remove_projects({'ids':[self.p.name],'delete_files':'yes'})
        self.assertTrue((self.p/'text').is_dir());self.assertNotIn(self.p.name,self.listed())

if __name__=='__main__':unittest.main()
