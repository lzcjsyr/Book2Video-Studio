"""Assembly with Claude Code: the agent gets the materials and one GPU rendering tool, and delivers the video."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import editing
from server import Studio,atomic_json
DESKTOP=Path(__file__).resolve().parents[2];ENGINE=DESKTOP/'build/engine'


def ffmpeg(*args):
    subprocess.run([shutil.which('ffmpeg') or 'ffmpeg','-nostdin','-y','-v','error',*map(str,args)],check=True)


class EditingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.studio=Studio(ENGINE,root/'data',root/'projects');self.p=self.studio.workspace/'剪辑 with spaces'
        for d in ['text','images','voice']:(self.p/d).mkdir(parents=True)
        raw={'source_name':'测试之书','content':'第一段。第二段。','total_length':8,'video_titles':['视频'],'cover_titles':['封面'],'cover_subtitles':[],'golden_quotes':['开场金句。']}
        script={**raw,'actual_segments':2,'segments':[{'index':1,'content':'第一段。','visualizer':'image'},{'index':2,'content':'第二段。','visualizer':'image'}]}
        atomic_json(self.p/'text/raw.json',raw);atomic_json(self.p/'text/script.json',script)
        from PIL import Image
        for i in [1,2]:
            Image.new('RGB',(320,180),(30*i,60,120)).save(self.p/f'images/segment_{i}.png')
            ffmpeg('-f','lavfi','-i',f'sine=frequency={400+i*100}:duration=1','-ac','1','-ar','48000',self.p/f'voice/voice_{i}.wav')
        state=self.studio.state(self.p);state['settings']={'global':{'opening_quote':False},'step5':{'assembly_method':'agent','agent_skill':'editorial-explainer','video_size':'320x180','narration_speed_factor':1.1,'enable_subtitles':True,'bgm_filename':None},'three_by_four':{'enabled':False}}
        self.studio.persist(self.p,state);self.id=self.studio.project_id(self.p)
    def tearDown(self):self.tmp.cleanup()
    def prepare(self,**body):
        launch=self.studio.prepare_agent(self.id,{'step':'5',**body});return launch,self.p/launch['assembly_edit']['folder']
    def settings(self,**step5):
        state=self.studio.state(self.p);state['settings']['step5'].update(step5);self.studio.persist(self.p,state)

    def test_the_agent_gets_the_materials_and_only_a_gpu_tool(self):
        launch,work=self.prepare();found=editing.read(work/'materials.json');prompt=Path(launch['context']).read_text()
        segment=found['segments'][0]
        self.assertEqual((segment['index'],segment['text'],segment['visual_kind']),(1,'第一段。','image'))
        self.assertEqual(Path(segment['audio']),(self.p/'voice/voice_1.wav').resolve());self.assertAlmostEqual(segment['audio_seconds'],1,delta=.05)
        self.assertEqual(Path(segment['visual']),(self.p/'images/segment_1.png').resolve())
        self.assertEqual((found['video'],found['narration_speed'],found['subtitles'],found['opening']),({'width':320,'height':180},1.1,True,None))
        # No plan format, renderer, styles or captions from Studio: only the materials and studio_render.py.
        self.assertEqual(sorted(p.name for p in work.iterdir()),['materials.json','studio_render.py','vendor'])
        for text in [str(work/'materials.json'),str(self.p/'final_video.mp4'),'320×180','1.10×','studio_render.py render','h264_videotoolbox']:self.assertIn(text,prompt)
        for text in ['plan.json','captions','overlays']:self.assertNotIn(text,prompt)
        args=launch['args'];self.assertEqual(args[args.index('--add-dir')-1],editing.KICKOFF[False]);self.assertEqual(launch['cwd'],str(self.p))
        self.assertEqual(args[args.index('--allowedTools')+1:args.index('--session-id')],editing.ALLOWED_TOOLS)
        self.assertEqual(launch['assembly_edit']['delivered_before'],None)

    def test_the_opening_is_material_not_a_design(self):
        state=self.studio.state(self.p);state['settings']['global']['opening_quote']=True;self.studio.persist(self.p,state)
        ffmpeg('-f','lavfi','-i','sine=frequency=300:duration=2','-ac','1','-ar','48000',self.p/'voice/opening.wav')
        with self.assertRaisesRegex(ValueError,'opening needs'):self.prepare()
        shutil.copy2(self.p/'images/segment_1.png',self.p/'images/opening_bg.png')
        launch,work=self.prepare();opening=editing.read(work/'materials.json')['opening']
        self.assertEqual((opening['quote'],opening['source_name']),('开场金句。','测试之书'))
        self.assertEqual(Path(opening['background']),(self.p/'images/opening_bg.png').resolve());self.assertAlmostEqual(opening['audio_seconds'],2,delta=.05)
        self.assertFalse((work/'opening').exists());self.assertIn('开场画面由你设计',Path(launch['context']).read_text())

    def test_music_follows_the_suggestion_or_the_whole_library(self):
        names=[]
        for name,frequency in [('lead.wav',500),('other.wav',700)]:
            tone=Path(self.tmp.name)/name;ffmpeg('-f','lavfi','-i',f'sine=frequency={frequency}:duration=1','-ar','48000',tone)
            names.append(self.studio.import_music({'path':str(tone)})['name'])
        self.settings(bgm_filename=names[0])
        launch,work=self.prepare();music=editing.read(work/'materials.json')['music']
        self.assertEqual([t['name'] for t in music['tracks']],[names[0]]);self.assertIn(f'只用「{names[0]}」',Path(launch['context']).read_text())
        self.studio.finish_agent(self.id,{})
        self.settings(agent_music_mix=True)
        self.assertFalse(self.studio.detail(self.id)['assembly_edit']['current'])
        launch,work=self.prepare();music=editing.read(work/'materials.json')['music']
        self.assertTrue(set(names)<={t['name'] for t in music['tracks']});self.assertIn('可以从清单里另选曲目',Path(launch['context']).read_text())

    def test_delivering_the_video_finishes_assembly(self):
        _,work=self.prepare()
        detail=self.studio.finish_agent(self.id,{})
        self.assertIn('without delivering',detail['warning']);self.assertIn('5',detail['state']['stale'])
        launch,work=self.prepare(revise=True)
        ffmpeg('-f','lavfi','-i','color=c=black:s=320x180:d=1','-pix_fmt','yuv420p',self.p/'final_video.mp4')
        (self.p/'final_video_3x4.mp4').write_bytes(b'an older 3:4 copy')
        detail=self.studio.finish_agent(self.id,{})
        self.assertIsNone(detail['warning']);self.assertNotIn('5',detail['state']['stale'])
        self.assertTrue(detail['assembly_edit']['delivered']);self.assertTrue(detail['state']['three_by_four_stale'])
        # The next session keeps the delivered video in revision history before Claude Code replaces it.
        self.prepare(revise=True)
        self.assertIn('final_video.mp4',' '.join(self.studio.state(self.p)['events'][0]['files']))

    def test_an_interrupted_session_that_delivered_counts_as_finished(self):
        self.prepare()
        ffmpeg('-f','lavfi','-i','color=c=black:s=320x180:d=1','-pix_fmt','yuv420p',self.p/'final_video.mp4')
        # The app quits while Claude Code is still open; the next start finds the session's owner gone.
        state=self.studio.state(self.p);state['agent_active']['owner']=999999;state['stale']['5']={'reason':'Not assembled','segments':[]};self.studio.persist(self.p,state)
        restarted=Studio(ENGINE,self.studio.data,self.studio.workspace).state(self.p)
        self.assertNotIn('agent_active',restarted);self.assertNotIn('5',restarted['stale'])
        # Without a delivery, the step stays marked for review.
        os.remove(self.p/'final_video.mp4');self.prepare(revise=True)
        state=self.studio.state(self.p);state['agent_active']['owner']=999999;self.studio.persist(self.p,state)
        self.assertIn('5',Studio(ENGINE,self.studio.data,self.studio.workspace).state(self.p)['stale'])

    def test_a_revision_continues_in_a_copy_of_the_last_folder(self):
        _,first=self.prepare();(first/'my_design.html').write_text('<p>mine</p>');self.studio.finish_agent(self.id,{})
        launch,revised=self.prepare(revise=True)
        self.assertNotEqual(first,revised);self.assertEqual((revised/'my_design.html').read_text(),'<p>mine</p>')
        prompt=Path(launch['context']).read_text()
        self.assertIn(editing.KICKOFF[True],launch['args']);self.assertIn('修改上一次的合成',prompt)
        # Materials are refreshed for the revision; the copy does not change the earlier folder.
        (revised/'my_design.html').write_text('<p>changed</p>');self.assertEqual((first/'my_design.html').read_text(),'<p>mine</p>')

    def test_the_gpu_tool_renders_and_snapshots_a_page(self):
        _,work=self.prepare()
        page='<!doctype html><style>html,body{margin:0;width:320px;height:180px;overflow:hidden;background:#123}</style><div id="b" style="position:absolute;width:40px;height:40px;background:#fc6;top:70px"></div><script>const b=document.getElementById("b");const p=window.clipPayload;window.clip={width:p.width,height:p.height,durationSeconds:p.durationSeconds,ready:Promise.resolve(),renderAt(t){b.style.left=(t*200)+"px"}};</script>'
        (work/'title.html').write_text(page)
        tool=[sys.executable,str(work/'studio_render.py')]
        result=subprocess.run([*tool,'render','title.html','--seconds','1','--output','title.mp4'],cwd=work,capture_output=True,text=True,timeout=180)
        self.assertEqual(result.returncode,0,result.stderr[-2000:])
        report=json.loads(result.stdout.strip().splitlines()[-1]);self.assertEqual(report['frames'],30);self.assertTrue((work/'title.mp4').is_file())
        snapshot=subprocess.run([*tool,'snapshot','title.html','--seconds','1','--at','0,0.5'],cwd=work,capture_output=True,text=True,timeout=120)
        self.assertEqual(snapshot.returncode,0,snapshot.stderr[-2000:]);self.assertEqual(len(list((work/'snapshots/title').glob('*.png'))),2)
        outside=subprocess.run([*tool,'render','../../../text/raw.json','--seconds','1','--output','x.mp4'],cwd=work,capture_output=True,text=True,timeout=60)
        self.assertNotEqual(outside.returncode,0)

    def test_two_pages_render_at_the_same_time_in_one_folder(self):
        _,work=self.prepare()
        for name,colour in [('one','#c33'),('two','#3c3')]:
            (work/f'{name}.html').write_text(f'<!doctype html><style>html,body{{margin:0;width:320px;height:180px;background:{colour}}}</style><div id="b"></div><script>const p=window.clipPayload;window.clip={{width:p.width,height:p.height,durationSeconds:p.durationSeconds,ready:Promise.resolve(),renderAt(t){{document.getElementById("b").textContent=t.toFixed(2)}}}};</script>')
        tool=[sys.executable,str(work/'studio_render.py')]
        runs=[subprocess.Popen([*tool,'render',f'{n}.html','--seconds','2','--output',f'{n}.mp4'],cwd=work,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for n in ['one','two']]
        for run in runs:
            _,errors=run.communicate(timeout=240);self.assertEqual(run.returncode,0,errors[-1500:])
        for n in ['one','two']:self.assertEqual(json.loads((work/f'{n}.render.json').read_text())['frames'],60)

    def test_free_design_leaves_the_style_to_the_agent(self):
        self.settings(agent_skill='content-led')
        launch,_=self.prepare();prompt=Path(launch['context']).read_text()
        skill=self.studio.library_root/'skills/step5/content-led/SKILL.md'
        self.assertIn(str(skill),prompt);self.assertIn('不预设风格',skill.read_text())

    def test_renders_running_side_by_side_do_not_trip_each_other(self):
        sys.path.insert(0,str(ENGINE))
        from core.infra.motion.artifacts import signature
        _,work=self.prepare();(work/'a.html').write_text('<p>a</p>');payload={'width':320,'height':180,'durationSeconds':1}
        before=signature(work,payload,entry='a.html',ignore_media=True)
        # Another render (or the agent's own encode) writing a video into the folder is not a change to this page.
        (work/'other.mp4').write_bytes(b'video');(work/'mix.wav').write_bytes(b'audio')
        self.assertEqual(signature(work,payload,entry='a.html',ignore_media=True),before)
        self.assertNotEqual(signature(work,payload,entry='a.html'),before)
        # A changed page or script still counts.
        (work/'engine.js').write_text('changed');self.assertNotEqual(signature(work,payload,entry='a.html',ignore_media=True),before)

    def test_both_agent_environments_use_writings_model(self):
        state=self.studio.state(self.p);state['settings']['step1']={'llm_server':'deepseek','llm_model':'deepseek-chat'};self.studio.persist(self.p,state)
        code="from pathlib import Path;from agent_env import resolve;import sys;a=resolve(Path(sys.argv[1]),Path(sys.argv[2]),'1');b=resolve(Path(sys.argv[1]),Path(sys.argv[2]),'5');assert a==b;assert b['ANTHROPIC_AUTH_TOKEN']=='editing-test-key'"
        result=subprocess.run([sys.executable,'-c',code,str(ENGINE),str(self.p)],cwd=DESKTOP/'backend',env={**os.environ,'DEEPSEEK_API_KEY':'editing-test-key'},capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,'Assembly and Writing must resolve the same provider environment')


if __name__=='__main__':
    unittest.main()
