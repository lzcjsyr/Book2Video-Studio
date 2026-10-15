"""Cover agent contracts, retained candidates, and real bundled WebGL rendering."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import covers
from server import Studio, atomic_json, engine_settings

DESKTOP = Path(__file__).resolve().parents[2]
ENGINE = DESKTOP / 'build/engine'
RAW = {'source_name': '测试主题', 'content': '关于经济预期的讲解。', 'total_length': 11,
       'video_titles': ['视频标题'], 'cover_titles': ['封面标题'], 'cover_subtitles': ['副标题'], 'golden_quotes': []}

HTML = '''<!doctype html><meta charset="utf-8"><style>
* {margin:0} body {background:#152532;color:white;font-family:sans-serif} canvas {position:absolute;inset:0}
h1 {position:absolute;left:8%;top:12%;font-size:36px}</style><h1>封面标题</h1>
<script>window.__ready=false</script>
<script type="importmap">{"imports":{"three":"./vendor/three/build/three.module.js"}}</script>
<script type="module">
import * as THREE from 'three';
const renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});
renderer.setPixelRatio(1); renderer.setSize(innerWidth,innerHeight);
document.body.prepend(renderer.domElement);
const scene=new THREE.Scene();scene.background=new THREE.Color('#152532');
const camera=new THREE.PerspectiveCamera(45,innerWidth/innerHeight,.1,100);camera.position.z=4;
const mesh=new THREE.Mesh(new THREE.TorusKnotGeometry(.7,.2,64,8),new THREE.MeshNormalMaterial());
scene.add(mesh);mesh.rotation.set(.4,.3,0);await document.fonts.ready;
renderer.render(scene,camera);window.__ready=true;
</script>'''

class CoverAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.studio = Studio(ENGINE, Path(self.temp.name)/'data', Path(self.temp.name)/'projects')
        self.p = self.studio.workspace/'封面 with spaces'
        for directory in ['text','voice','images']:
            (self.p/directory).mkdir(parents=True)
        atomic_json(self.p/'text/raw.json', copy.deepcopy(RAW))
        state = self.studio.state(self.p)
        state['settings'] = {'step6': {'cover_method':'agent', 'cover_image_size_presets':{'16:9':'640x360','3:4':'360x480'},
                                     'cover_image_size_keys':['16:9','3:4'], 'cover_image_count':1, 'cover_image_style':'cover01'}}
        self.studio.persist(self.p,state)
        self.id = self.studio.project_id(self.p)
    def tearDown(self):
        self.temp.cleanup()
    def activate(self, **body):
        return self.studio.prepare_agent(self.id, {'step':'6', **body})
    def compose(self, launch):
        work = Path(launch['cwd'])
        for entry in launch['cover_design']['entries']:
            (work/entry['html']).write_text(HTML)
        return work
    def test_context_has_canonical_copy_local_three_and_every_size(self):
        launch = self.activate()
        prompt = Path(launch['context']).read_text()
        self.assertIn('封面标题', (Path(launch['cwd'])/'studio-payload.json').read_text())
        self.assertIn('cover_640x360_1.html',prompt)
        self.assertIn('cover_360x480_1.html',prompt)
        self.assertIn('window.__ready',prompt)
        self.assertIn('python render.py',prompt)
        self.assertTrue((Path(launch['cwd'])/'vendor/three/build/three.module.js').is_file())
        self.assertEqual(engine_settings({'step6':{'cover_method':'agent','cover_image_count':2}}),{'step6':{'cover_image_count':2}})
    def test_missing_design_warns_and_keeps_existing_cover(self):
        (self.p/'cover_existing.png').write_bytes(b'previous')
        self.activate()
        result = self.studio.finish_agent(self.id,{})
        self.assertIn('Save every cover HTML', result['warning'])
        self.assertNotIn('agent_active',result['state'])
        self.assertEqual(result['covers'],['cover_existing.png'])
        with self.assertRaisesRegex(ValueError,'Save every'):
            self.studio.run_job(self.id, {'step':'6','mode':'cover_render'})
    def test_saved_agent_template_is_used_by_new_and_revised_cover_sessions(self):
        library=self.studio.library
        image=library.item('templates','step6_cover.md')['value']
        original=library.item('templates','step6_cover_agent.md')
        def save(value):
            item=library.item('templates','step6_cover_agent.md')
            return library.save({'group':'templates','id':item['id'],'value':value,'expected_hash':item['hash']})
        save(original['value'].replace('<!-- section: main -->','<!-- section: main -->\nCUSTOM COVER DIRECTION V1'))
        first=self.activate();first_prompt=Path(first['context']).read_text()
        self.assertIn('CUSTOM COVER DIRECTION V1',first_prompt)
        self.assertIn(first['cwd'],first_prompt)
        self.assertIn('cover_360x480_1.html',first_prompt)
        self.assertIn('{"imports":{"three":',first_prompt)
        self.compose(first);self.studio.finish_agent(self.id,{})
        revision=save(original['value'].replace('<!-- section: main -->','<!-- section: main -->\nCUSTOM COVER DIRECTION V2'))['revision']
        second=self.activate(revise=True);second_prompt=Path(second['context']).read_text()
        self.assertIn('CUSTOM COVER DIRECTION V2',second_prompt)
        self.assertNotIn('CUSTOM COVER DIRECTION V1',second_prompt)
        self.assertIn(library.prompt('step6_cover_agent.md', 'revise'),second_prompt)
        self.assertEqual(Path(first['context']).read_text(),first_prompt)
        self.assertEqual(library.item('templates','step6_cover.md')['value'],image)
        library.restore({'revision':revision})
        self.assertIn('CUSTOM COVER DIRECTION V1',library.item('templates','step6_cover_agent.md')['value'])

    def test_invalid_agent_template_is_rejected_before_a_session_is_created(self):
        item=self.studio.library.item('templates','step6_cover_agent.md')
        with self.assertRaisesRegex(ValueError,'variables'):
            self.studio.library.save({'group':'templates','id':item['id'],'value':item['value'].replace('{entries_json}',''),'expected_hash':item['hash']})
        Path(item['path']).write_text('Invalid external edit {unknown}')
        with self.assertRaisesRegex(ValueError,'sections'):self.activate()
        self.assertNotIn('agent_active',self.studio.state(self.p))
    def test_revisions_copy_html_keep_original_and_refresh_previews(self):
        launch = self.activate(); work = self.compose(launch)
        first = self.studio.detail(self.id)
        (work/'previews').mkdir(); (work/'previews/cover_640x360_1.png').write_bytes(b'preview')
        second = self.studio.detail(self.id)
        self.assertNotEqual(first['revision'],second['revision'])
        self.assertEqual(len(second['cover_design']['previews']),1)
        result = self.studio.finish_agent(self.id,{})
        self.assertIsNone(result['warning'])
        self.assertIn('6',result['state']['stale'])
        revised = self.activate(revise=True); revised_work = Path(revised['cwd'])
        self.assertNotEqual(work,revised_work)
        self.assertEqual((revised_work/'cover_640x360_1.html').read_text(),HTML)
        (revised_work/'cover_640x360_1.html').write_text('revised')
        self.assertEqual((work/'cover_640x360_1.html').read_text(),HTML)
    def test_changed_copy_or_size_requires_a_revision(self):
        launch=self.activate();self.compose(launch);self.studio.finish_agent(self.id,{})
        self.assertTrue(self.studio.detail(self.id)['cover_design']['current'])
        raw=copy.deepcopy(RAW);raw['cover_titles']=['新的标题'];atomic_json(self.p/'text/raw.json',raw)
        self.assertFalse(self.studio.detail(self.id)['cover_design']['current'])
        with self.assertRaisesRegex(ValueError,'setup changed'):
            self.studio.run_job(self.id,{'step':'6','mode':'cover_render'})
    def test_api_mode_remains_default_and_agent_requires_selection(self):
        state=self.studio.state(self.p);state['settings']['step6'].pop('cover_method');self.studio.persist(self.p,state)
        with self.assertRaisesRegex(ValueError,'Choose Claude Code'):
            self.activate()
        self.assertNotIn('agent_active',self.studio.state(self.p))
    def test_cover_environment_uses_writings_provider_and_model(self):
        import os
        state=self.studio.state(self.p)
        state['settings']['step1']={'llm_server':'deepseek','llm_model':'deepseek-chat'}
        self.studio.persist(self.p,state)
        probe="from agent_env import resolve; from pathlib import Path; import sys; a=resolve(Path(sys.argv[1]),Path(sys.argv[2]),'1'); b=resolve(Path(sys.argv[1]),Path(sys.argv[2]),'6'); assert a == b; assert b['ANTHROPIC_AUTH_TOKEN']=='cover-provider-test'"
        result=subprocess.run([sys.executable,'-c',probe,str(ENGINE),str(self.p)],cwd=DESKTOP/'backend',
                              env={**os.environ,'DEEPSEEK_API_KEY':'cover-provider-test'},capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,'Cover environment must match the writing environment')
    def test_real_webgl_preview_and_final_pngs_have_exact_sizes_and_keep_candidates(self):
        from PIL import Image, ImageStat
        launch=self.activate();work=self.compose(launch)
        covers.render(self.p,launch['cover_design'],preview=True)
        for entry in launch['cover_design']['entries']:
            png=work/'previews'/(Path(entry['html']).stem+'.png')
            with Image.open(png) as image:
                self.assertEqual(image.size,(entry['width'],entry['height']))
                self.assertGreater(max(ImageStat.Stat(image.convert('RGB')).stddev),10)
        self.studio.finish_agent(self.id,{})
        previous=self.p/'cover_existing.png';previous.write_bytes(b'previous')
        first=covers.render(self.p,launch['cover_design'])
        second=covers.render(self.p,launch['cover_design'])
        self.assertFalse(set(first['covers']) & set(second['covers']))
        self.assertEqual(previous.read_bytes(),b'previous')
        self.assertEqual(len(self.studio.detail(self.id)['covers']),5)
    def test_missing_asset_fails_without_publishing_partial_covers(self):
        launch=self.activate();work=self.compose(launch)
        second=work/launch['cover_design']['entries'][1]['html']
        second.write_text('<script src="missing.js"></script><script>window.__ready=true</script>')
        with self.assertRaisesRegex(ValueError,'Asset HTTP 404'):
            covers.render(self.p,launch['cover_design'])
        self.assertEqual(list(self.p.glob('cover_*.png')),[])
    def test_dimensions_and_paths_are_validated(self):
        for size in ['0x100','8193x100','abc','-1x100']:
            with self.assertRaises(ValueError):covers.dimensions(size)
        with self.assertRaises(ValueError):covers.contained(self.p,'../escape')
        state=self.studio.state(self.p);state['settings']['step6']['cover_image_count']=0
        self.studio.persist(self.p,state)
        with self.assertRaisesRegex(ValueError,'between 1 and 20'):self.activate()
