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
        for step,name,section in [('1','step1_studio.md','shared'),('4','step4_studio.md','finish'),
                                  ('5','step5_editing_agent.md','main'),('6','step6_cover_agent.md','main')]:
            with self.subTest(step=step):
                marker=f'CUSTOM STEP {step} INSTRUCTIONS'
                revision=self.save(name,lambda value:value.replace(f'<!-- section: {section} -->',f'<!-- section: {section} -->\n{marker}'))['revision']
                first=self.launch(step);prompt=Path(first['context']).read_text();self.assertIn(marker,prompt)
                self.studio.library.restore({'revision':revision})
                second=self.launch(step);self.assertNotIn(marker,Path(second['context']).read_text())
                self.assertEqual(Path(first['context']).read_text(),prompt)

    def test_motion_reference_style_and_assembly_contract_instructions_are_editable(self):
        for name in ['step4_skill_context.md','step4_style_context.md','step5_editing_contract.md']:
            self.save(name,lambda value:value+'\nCUSTOM '+name+'\n')
        motion=self.launch('4');prompt=Path(motion['context']).read_text()
        for name in ['step4_skill_context.md','step4_style_context.md']:self.assertIn('CUSTOM '+name,prompt)
        assembly=self.launch('5')
        self.assertIn('CUSTOM step5_editing_contract.md',(Path(assembly['cwd'])/'EDITING.md').read_text())

    def test_missing_sections_and_variables_are_rejected_without_saving(self):
        item=self.studio.library.item('templates','step4_studio.md')
        for transform in [lambda value:value.replace('<!-- section: single -->','<!-- section: wrong -->'),
                          lambda value:value.replace('{hyperframes_version}','')]:
            with self.assertRaisesRegex(ValueError,'sections|variables'):self.save(item['id'],transform)
        self.assertEqual(self.studio.library.item('templates',item['id'])['value'],item['value'])
