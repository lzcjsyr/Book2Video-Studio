"""Every desktop agent reads editable instructions when a session is prepared."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from context import prepare
from server import Studio, atomic_json

ENGINE = Path(__file__).resolve().parents[2] / 'build/engine'


class AgentTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();root = Path(self.temp.name)
        self.studio = Studio(ENGINE, root/'data', root/'projects');self.p = root/'projects/story'
        for directory in ['text', 'voice', 'images']:(self.p/directory).mkdir(parents=True)
        raw = {'source_name':'Template QA','content':'Hello world.','total_length':12,'video_titles':['Video'],
               'cover_titles':['Cover'],'cover_subtitles':['Subtitle'],'golden_quotes':[]}
        atomic_json(self.p/'text/raw.json', raw)
        atomic_json(self.p/'text/script.json', {**raw,'segments':[{'index':1,'content':raw['content'],'visualizer':'image'}]})
        atomic_json(self.p/'text/mini_summary.json', {'summary':'Template QA summary'})
        with wave.open(str(self.p/'voice/voice_1.wav'), 'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*16000)
        from PIL import Image
        Image.new('RGB',(320,180),'blue').save(self.p/'images/segment_1.png')
        state=self.studio.state(self.p);state['settings']={'global':{'opening_quote':False},
            'step1':{'writing_mode':'direct'},'step5':{'assembly_method':'agent','video_size':'320x180'},
            'step6':{'cover_method':'agent','cover_image_size_presets':{'1:1':'320x320'},'cover_image_size_keys':['1:1']}}
        self.studio.persist(self.p,state)

    def tearDown(self):self.temp.cleanup()

    def save(self, name, transform):
        item=self.studio.library.item('templates',name)
        return self.studio.library.save({'group':'templates','id':name,'value':transform(item['value']),'expected_hash':item['hash']})

    def launch(self, step):
        return prepare(self.studio,self.p,{'step':step,'indices':[1], 'folders':{1:self.p/'images/takes/motion'}})

    def test_each_agents_studio_instructions_are_editable_and_reload_without_restart(self):
        for step,name,section in [('1','step1_agent.md','main'),('4','step4_motion_agent.md','main'),
                                  ('5','step5_editing_agent.md',None),('6','step6_cover_agent.md','main')]:
            with self.subTest(step=step):
                marker=f'CUSTOM STEP {step} INSTRUCTIONS'
                revision=self.save(name,lambda value:value+'\n'+marker if section is None else
                                   value.replace(f'<!-- section: {section} -->',f'<!-- section: {section} -->\n{marker}'))['revision']
                first=self.launch(step);prompt=Path(first['context']).read_text();self.assertIn(marker,prompt)
                self.studio.library.restore({'revision':revision})
                second=self.launch(step);self.assertNotIn(marker,Path(second['context']).read_text())
                self.assertEqual(Path(first['context']).read_text(),prompt)

    def test_shared_rules_open_every_session_and_are_editable(self):
        marker='CUSTOM SHARED RULE'
        self.save('agent_rules.md',lambda value:value.replace('<!-- section: rules -->','<!-- section: rules -->\n'+marker))
        for step in ['1','4','5','6']:
            with self.subTest(step=step):
                prompt=Path(self.launch(step)['context']).read_text()
                # The rules come first, then the step's own template; notes for people never reach the agent.
                self.assertTrue(prompt.startswith(marker));self.assertNotIn('<!--',prompt)

    def test_a_session_prompt_is_the_rules_and_its_template_with_values_filled_in(self):
        launch=self.launch('6');prompt=Path(launch['context']).read_text()
        rules=self.studio.library.prompt('agent_rules.md','rules',task_file=Path(launch['task'])).strip()
        self.assertTrue(prompt.startswith(rules))
        for text in ['Cover','Subtitle','cover_320x320_1.html：320×320',launch['cwd']]:self.assertIn(text,prompt)
        self.assertNotIn('{',prompt.replace('{{','').split('importmap')[0])

    def test_the_assembly_template_is_one_file_with_every_setting_filled_in(self):
        from library import PROMPTS
        assembly=self.launch('5');prompt=Path(assembly['context']).read_text()
        work=Path(assembly['cwd'])/assembly['assembly_edit']['folder']
        # Studio fills every variable: the materials, the person's choices, delivery and the GPU guide are all in this one prompt.
        for field in PROMPTS['step5_editing_agent.md'][2]:self.assertNotIn('{'+field+'}',prompt)
        for text in ['## 用户的选择','320×180','## 交付','## GPU 加速',str(work),str(work/'materials.json'),'editorial-explainer/SKILL.md']:self.assertIn(text,prompt)
        for gone in ['EDITING.md','plan.json','studio-payload.json']:self.assertFalse((work/gone).exists())
        # A template that loses one of its settings is refused rather than silently dropping it.
        with self.assertRaisesRegex(ValueError,'required template variables'):
            self.save('step5_editing_agent.md',lambda value:value.replace('{music}','music'))

    def test_no_skill_writing_keeps_output_contract_without_loading_skill_files(self):
        import shutil
        # No fallback skill can accidentally make the no-skill session succeed.
        shutil.rmtree(self.studio.library_root/'skills/step1')
        source = self.p/'source.txt'; source.write_text('A real source document.')
        for mode in ['direct', 'source']:
            with self.subTest(mode=mode):
                previous = self.studio.writing_options(self.studio.state(self.p))
                options = {**previous, 'mode': mode, 'source': str(source), 'agent_skill': ''}
                saved = self.studio.save_writing(self.p.name, {'options': options, 'expected': previous})
                self.assertEqual(saved['state']['settings']['step1']['agent_skill'], '')
                self.assertEqual(self.studio.writing_options(self.studio.state(self.p))['agent_skill'], '')
                launch = prepare(self.studio, self.p, {'step': '1', 'instructions': 'Write a short, warm introduction.'})
                prompt = Path(launch['context']).read_text()
                self.assertIn('没有选择写作技能', prompt)
                self.assertIn('Write a short, warm introduction.', prompt)
                self.assertIn(str(self.p/'text/raw.json'), prompt)
                for field in ['source_name', 'content', 'total_length', 'video_titles', 'golden_quotes']:
                    self.assertIn(field, prompt)
                self.assertNotIn(str(self.studio.library_root/'skills'), prompt)
                self.assertNotIn(str(self.studio.library_root/'skills'), launch['args'])
                self.assertNotIn('{skill_path}', prompt)
                if mode == 'source':
                    self.assertIn(str(source), prompt)
                    self.assertIn(str(source.parent), launch['args'])
                else:
                    self.assertNotIn(str(source), prompt)

    def test_every_interactive_agent_has_a_scoped_source_manifest(self):
        for step in ['1', '4', '5', '6']:
            with self.subTest(step=step):
                launch = self.launch(step)
                task = json.loads(Path(launch['task']).read_text())
                prompt = Path(launch['context']).read_text()
                self.assertEqual(task['stage'], step)
                self.assertEqual(task['cwd'], str(Path(launch['cwd']).resolve()))
                self.assertTrue(task['writable']); self.assertTrue(task['protected'])
                self.assertIn(str(Path(launch['task'])), prompt)
                self.assertIn('不自行进入下一步', prompt)
                self.assertEqual(launch['context_chars'], len(prompt))
                self.assertNotIn(str(self.studio.library_root/'skills'), launch['args'])
                self.assertNotIn('ANTHROPIC_API_KEY', json.dumps(task))
                if step in {'4', '6'}:
                    self.assertIn(str((Path(launch['cwd'])/'render.py').resolve()), task['protected'])
                if step == '6':
                    payload=json.loads((Path(launch['cwd'])/'studio-payload.json').read_text())
                    self.assertNotIn('manuscript', payload)
                    self.assertEqual(payload['manuscript_path'], str(self.p/'text/raw.json'))

    def test_motion_instructions_are_shared_once_and_task_payloads_remain_distinct(self):
        first=self.launch('4')
        one=Path(first['context']).read_text()
        script=json.loads((self.p/'text/script.json').read_text())
        script['segments'].append({'index':2,'content':'A second, unrelated narration.','visualizer':'hf'})
        atomic_json(self.p/'text/script.json',script)
        with wave.open(str(self.p/'voice/voice_2.wav'),'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*32000)
        folders={1:self.p/'images/takes/batch-1',2:self.p/'images/takes/batch-2'}
        launch=prepare(self.studio,self.p,{'step':'4','indices':[1,2],'folders':folders})
        prompt=Path(launch['context']).read_text()
        task=json.loads(Path(launch['task']).read_text())
        self.assertEqual(prompt.count('## 片段契约'),1)
        self.assertEqual(prompt.count('动作配方：'),1)
        self.assertNotIn('A second, unrelated narration.',prompt)
        self.assertLess(len(prompt),len(one)*1.5)
        self.assertEqual(task['facts']['selected_segments'],[1,2])
        self.assertEqual(task['writable'],[str(p.resolve()) for p in folders.values()])
        for index, work in folders.items():
            payload=json.loads((work/'studio-payload.json').read_text())
            self.assertEqual(payload['segmentIndex'],index)
            self.assertEqual(payload['durationSeconds'],float(index))
            self.assertIn(str(work/'studio-payload.json'),prompt)
        self.assertNotEqual(json.loads((folders[1]/'studio-payload.json').read_text())['content'],json.loads((folders[2]/'studio-payload.json').read_text())['content'])

    def test_missing_sections_and_variables_are_rejected_without_saving(self):
        item=self.studio.library.item('templates','step4_motion_agent.md')
        for transform in [lambda value:value.replace('<!-- section: segment -->','<!-- section: wrong -->'),
                          lambda value:value.replace('{work_dir}','')]:
            with self.assertRaisesRegex(ValueError,'sections|variables'):self.save(item['id'],transform)
        self.assertEqual(self.studio.library.item('templates',item['id'])['value'],item['value'])
