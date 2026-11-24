"""Assembly's execution contract and the documented drawing API, without an LLM."""
import json
import os
from pathlib import Path
import re
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
        state=self.studio.state(self.p);state['settings']={'global':{'opening_quote':False},'step5':{'assembly_method':'agent','agent_skill':'editorial-explainer','video_size':'320x180','narration_speed_factor':1.1,'enable_subtitles':True,'bgm_filename':None,'agent_music_mix':False},'three_by_four':{'enabled':False}}
        self.studio.persist(self.p,state);self.id=self.studio.project_id(self.p)
    def tearDown(self):self.tmp.cleanup()
    def prepare(self,**body):
        launch=self.studio.prepare_agent(self.id,{'step':'5',**body});return launch,self.p/launch['assembly_edit']['folder']
    def settings(self,**step5):
        state=self.studio.state(self.p);state['settings']['step5'].update(step5);self.studio.persist(self.p,state)

    def api_worker(self, launch, work):
        # Exercise the template's exact import example in an agent-owned script.
        bootstrap=re.findall(r'(?ms)^```python\n(.*?)^```',Path(launch['context']).read_text())[0]
        worker='''
import argparse
parser=argparse.ArgumentParser()
parser.add_argument('command')
parser.add_argument('html',nargs='?')
parser.add_argument('--seconds',type=float,default=1)
parser.add_argument('--output',default='sample.mp4')
parser.add_argument('--at',default='0')
args=parser.parse_args()
if args.command=='capabilities':
    report=inspect_machine(work,size,fps=30)
else:
    source=(work/args.html).resolve()
    assert source.is_relative_to(work) and source.is_file() and source.suffix=='.html'
    output=(work/args.output).resolve()
    assert output.is_relative_to(work)
    entry=source.relative_to(work).as_posix()
    payload=dict(size,durationSeconds=args.seconds,fps=30)
    if args.command=='snapshot':
        report=capture(work,payload,mode='snapshot',fps=30,times=list(map(float,args.at.split(','))),entry=entry,
                       caption_zone=False,destination=work/'build/snapshots'/source.stem)
    else:
        render_clip(work,payload,output,fps=30,entry=entry,caption_zone=False,ignore_media=True)
        report=json.loads(output.with_suffix('.render.json').read_text())
print(json.dumps(report))
'''
        (work/'run.py').write_text(bootstrap+worker,encoding='utf-8')
        return [sys.executable,str(work/'run.py')]

    def test_the_agent_gets_the_materials_and_contract_without_a_preset_runner(self):
        launch,work=self.prepare();found=editing.read(work/'materials.json');prompt=Path(launch['context']).read_text()
        segment=found['segments'][0]
        self.assertEqual((segment['index'],segment['text'],segment['visual_kind']),(1,'第一段。','image'))
        self.assertEqual(Path(segment['audio']),(self.p/'voice/voice_1.wav').resolve());self.assertAlmostEqual(segment['audio_seconds'],1,delta=.05)
        self.assertEqual(Path(segment['visual']),(self.p/'images/segment_1.png').resolve())
        self.assertEqual((found['video'],found['narration_speed'],found['subtitles'],found['opening']),({'width':320,'height':180},1.1,True,None))
        self.assertEqual(sorted(p.name for p in work.iterdir()),['materials.json','vendor'])
        for text in [str(work/'materials.json'),str(self.p/'final_video.mp4'),'320×180','1.10×','python run.py render','h264_videotoolbox']:self.assertIn(text,prompt)
        for text in ['plan.json','overlays']:self.assertNotIn(text,prompt)
        task=json.loads(Path(launch['task']).read_text())
        self.assertNotIn(str(work/'run.py'),task['protected'])
        self.assertIn(str(work),task['writable'])
        self.assertEqual(launch['cwd'], str(work))
        self.assertEqual(launch['agent'], 'claude')
        self.assertNotIn('first_message', launch)
        self.assertIn('只在用户要求时才开始剪辑', prompt)
        self.assertEqual(launch['assembly_edit']['delivered_before'],None)

    def test_opening_and_closing_a_revision_keeps_the_published_edit(self):
        self.prepare()
        empty = self.studio.finish_agent(self.id, {})
        self.assertIsNone(empty['warning'])
        self.assertIsNone(empty['assembly_edit'])
        self.assertNotIn('5', empty['state']['stale'])

        self.prepare()
        video = self.p / 'final_video.mp4'
        ffmpeg('-f', 'lavfi', '-i', 'color=c=black:s=320x180:d=1', '-pix_fmt', 'yuv420p', video)
        original = self.studio.finish_agent(self.id, {})['assembly_edit']
        state = self.studio.state(self.p)
        state['reviewed']['5'] = True
        self.studio.persist(self.p, state)
        stamp = editing.stamp(video)

        for exit_session in ('close', 'restart'):
            with self.subTest(exit_session=exit_session):
                self.prepare(revise=True)
                self.assertEqual(self.studio.detail(self.id)['assembly_edit'], original)
                if exit_session == 'close':
                    result = self.studio.finish_agent(self.id, {})
                    self.assertIsNone(result['warning'])
                else:
                    state = self.studio.state(self.p)
                    state['agent_active']['owner'] = 999999
                    self.studio.persist(self.p, state)
                    self.studio = Studio(ENGINE, self.studio.data, self.studio.workspace)
                    result = self.studio.detail(self.id)
                self.assertTrue(result['state']['reviewed']['5'])
                self.assertNotIn('5', result['state']['stale'])
                self.assertEqual(result['assembly_edit'], original)
                self.assertEqual(editing.stamp(video), stamp)

        _, work = self.prepare(revise=True)
        (work / 'run.py').write_text('# draft changes\n', encoding='utf-8')
        result = self.studio.finish_agent(self.id, {})
        self.assertIn('without delivering', result['warning'])
        self.assertEqual(result['assembly_edit']['folder'], str(work.relative_to(self.p)))
        self.assertEqual(editing.stamp(video), stamp)

    def test_agy_loads_the_assembly_template_as_a_workspace_agent(self):
        self.settings(agent_cli='agy')
        launch, work = self.prepare()
        self.assertEqual(launch['agent'], 'agy')
        self.assertNotIn('first_message', launch)
        definition = work / '.agents/agents/grist-assembly.md'
        self.assertIn(Path(launch['context']).read_text(encoding='utf-8'),
                      definition.read_text(encoding='utf-8'))

    def test_the_agent_gets_the_bundled_sound_effects(self):
        launch,work=self.prepare();found=editing.read(work/'materials.json');sounds=found['sound_effects']['sounds']
        self.assertGreaterEqual(len(sounds),20);self.assertIn('sound_effects',Path(launch['context']).read_text())
        for sound in sounds:
            self.assertTrue(Path(sound['path']).is_file(),sound['name'])
            self.assertTrue({'name','category','use','seconds','peak_at','suggested_gain_db'}<=set(sound),sound['name'])
            self.assertLessEqual(sound['peak_at'],sound['seconds'],sound['name'])
        # The library belongs to the app: changing it must not make the last edit look stale.
        current=editing.materials(self.studio,self.p,self.studio.state(self.p)['settings'])
        self.assertTrue(editing.detail(self.p,launch['assembly_edit'],{**current,'sound_effects':{'sounds':[]}})['current'])

    def test_the_agent_gets_each_recordings_timing(self):
        sentences=[{'text':'第一段。','start':0.1,'end':0.9,'words':[{'text':'第','start':0.1,'end':0.3,'confidence':0.9}]}]
        (self.p/'voice/voice_1.json').write_text(json.dumps({'seconds':1.0,'sentences':sentences}),encoding='utf-8')
        os.utime(self.p/'voice/voice_2.wav');(self.p/'voice/voice_2.json').write_text('{"sentences":[]}',encoding='utf-8')
        os.utime(self.p/'voice/voice_2.json',ns=(1,1))  # older than its recording: an earlier take's timing
        launch,work=self.prepare();found=editing.read(work/'materials.json')
        # The agent reads sentences in materials.json; character times are one line per sentence in timing.jsonl.
        self.assertEqual(found['segments'][0]['sentences'],[{'text':'第一段。','start':0.1,'end':0.9}])
        self.assertIsNone(found['segments'][1]['sentences']);self.assertNotIn('timing',found['segments'][0])
        self.assertEqual(found['word_timing'],'timing.jsonl')
        lines=[json.loads(line) for line in (work/'timing.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(lines,[{'part':1,'sentence':0,'text':'第一段。','start':0.1,'end':0.9,'words':[['第',0.1,0.3,0.9]]}])
        self.assertIn('timing.jsonl',Path(launch['context']).read_text())
        self.assertIn(str(work/'timing.jsonl'),json.loads(Path(launch['task']).read_text())['protected'])
        # Timing follows the recording: it alone never makes the last edit look stale.
        current=editing.materials(self.studio,self.p,self.studio.state(self.p)['settings'])
        stale={**current,'segments':[{**s,'timing':None} for s in current['segments']]}
        self.assertTrue(editing.detail(self.p,launch['assembly_edit'],stale)['current'])
        # Without any timing, a revision does not keep the earlier session's file.
        (self.p/'voice/voice_1.json').unlink();self.studio.finish_agent(self.id,{})
        launch,work=self.prepare(revise=True)
        self.assertFalse((work/'timing.jsonl').exists());self.assertIsNone(editing.read(work/'materials.json')['word_timing'])

    def test_the_render_tool_runs_as_claude_code_runs_it(self):
        # Capabilities must work before render.json exists, without PYTHONPATH.
        launch,work=self.prepare()
        tool=self.api_worker(launch,work)
        env={k:v for k,v in os.environ.items() if k!='PYTHONPATH'}
        result=subprocess.run([*tool,'capabilities'],cwd=work,env=env,capture_output=True,text=True,timeout=180)
        self.assertEqual(result.returncode,0,result.stdout[-1500:]+result.stderr[-1500:])
        self.assertTrue(result.stdout.strip())

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
        (work / 'run.py').write_text('# draft using the suggested music\n')
        self.studio.finish_agent(self.id,{})
        self.settings(agent_music_mix=True)
        self.assertFalse(self.studio.detail(self.id)['assembly_edit']['current'])
        launch,work=self.prepare();music=editing.read(work/'materials.json')['music']
        self.assertTrue(set(names)<={t['name'] for t in music['tracks']});self.assertIn('可以从清单里另选曲目',Path(launch['context']).read_text())

    def test_delivering_the_video_finishes_assembly(self):
        _,work=self.prepare()
        (work / 'run.py').write_text('# draft without a delivery\n')
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
        os.remove(self.p/'final_video.mp4');_,work=self.prepare(revise=True)
        (work / 'run.py').write_text('# draft without a delivery\n')
        state=self.studio.state(self.p);state['agent_active']['owner']=999999;self.studio.persist(self.p,state)
        self.assertIn('5',Studio(ENGINE,self.studio.data,self.studio.workspace).state(self.p)['stale'])

    def test_a_revision_continues_in_a_copy_of_the_last_folder(self):
        _,first=self.prepare();(first/'my_design.html').write_text('<p>mine</p>')
        (first/'run.py').write_text('# agent-owned runner\n')
        (first/'studio_render.py').write_text('# legacy app wrapper\n')
        self.studio.finish_agent(self.id,{})
        launch,revised=self.prepare(revise=True)
        self.assertNotEqual(first,revised);self.assertEqual((revised/'my_design.html').read_text(),'<p>mine</p>')
        self.assertFalse((revised/'studio_render.py').exists())
        self.assertEqual((first/'studio_render.py').read_text(),'# legacy app wrapper\n')
        self.assertEqual((revised/'run.py').read_text(),'# agent-owned runner\n')
        (revised/'run.py').write_text('# revised runner\n')
        self.assertEqual((first/'run.py').read_text(),'# agent-owned runner\n')
        prompt=Path(launch['context']).read_text()
        self.assertNotIn('first_message', launch);self.assertIn('修改上一次的合成',prompt);self.assertIn('按用户在对话里提出的要求修改',prompt)
        # Materials are refreshed for the revision; the copy does not change the earlier folder.
        (revised/'my_design.html').write_text('<p>changed</p>');self.assertEqual((first/'my_design.html').read_text(),'<p>mine</p>')

    def test_background_resume_keeps_completed_files_and_does_not_wait_for_user(self):
        from context import prepare
        _, first = self.prepare()
        (first / 'completed.html').write_text('<p>completed scene</p>')
        self.studio.finish_agent(self.id, {})
        launch = prepare(self.studio, self.p, {'step': '5', 'headless': True, 'revise': True})
        resumed = self.p / launch['assembly_edit']['folder']
        self.assertNotEqual(first, resumed)
        self.assertEqual((resumed / 'completed.html').read_text(), '<p>completed scene</p>')
        prompt = Path(launch['context']).read_text()
        self.assertIn('复用已验证的产物，只补缺失或失败部分', prompt)
        self.assertNotIn('然后等待用户的修改要求', prompt)

    def test_the_gpu_tool_renders_and_snapshots_a_page(self):
        launch,work=self.prepare()
        page='<!doctype html><style>html,body{margin:0;width:320px;height:180px;overflow:hidden;background:#123}</style><div id="b" style="position:absolute;width:40px;height:40px;background:#fc6;top:70px"></div><script>const b=document.getElementById("b");const p=window.clipPayload;window.clip={width:p.width,height:p.height,durationSeconds:p.durationSeconds,ready:Promise.resolve(),renderAt(t){b.style.left=(t*200)+"px"}};</script>'
        (work/'title.html').write_text(page)
        tool=self.api_worker(launch,work)
        result=subprocess.run([*tool,'render-page','title.html','--seconds','1','--output','title.mp4'],cwd=work,capture_output=True,text=True,timeout=180)
        self.assertEqual(result.returncode,0,result.stderr[-2000:])
        report=json.loads(result.stdout.strip().splitlines()[-1]);self.assertEqual(report['frames'],30);self.assertTrue((work/'title.mp4').is_file())
        snapshot=subprocess.run([*tool,'snapshot','title.html','--seconds','1','--at','0,0.5'],cwd=work,capture_output=True,text=True,timeout=120)
        self.assertEqual(snapshot.returncode,0,snapshot.stderr[-2000:]);self.assertEqual(len(list((work/'build/snapshots/title').glob('*.png'))),2)
        outside=subprocess.run([*tool,'render-page','../../../text/raw.json','--seconds','1','--output','x.mp4'],cwd=work,capture_output=True,text=True,timeout=60)
        self.assertNotEqual(outside.returncode,0)

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

    def test_assembly_helper_renders_composes_with_ass_and_delivers_atomically(self):
        sys.path.insert(0, str(ENGINE))
        from core.infra.motion.assembly import compose, run_manifest
        _, work = self.prepare()
        (work / 'scenes').mkdir(exist_ok=True)
        (work / 'build').mkdir(exist_ok=True)
        for name, colour in [('001', '#224466'), ('002', '#446622')]:
            (work / f'scenes/{name}.html').write_text(
                f'<!doctype html><style>html,body{{margin:0;width:320px;height:180px;overflow:hidden;background:{colour}}}</style>'
                '<div id="t" style="color:#fff;font:24px sans-serif"></div>'
                '<script>const p=window.clipPayload;window.clip={width:p.width,height:p.height,durationSeconds:p.durationSeconds,'
                'ready:Promise.resolve(),renderAt(t){document.getElementById("t").textContent=t.toFixed(2)}};</script>',
                encoding='utf-8',
            )
        ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-ac', '1', '-ar', '48000', work / 'build/audio.wav')
        ass = (
            '[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 180\n\n'
            '[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,'
            'Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n'
            'Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,12,1\n\n'
            '[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n'
            'Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,第一句\n'
            'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,第二句\n'
        )
        (work / 'build/captions.ass').write_text(ass, encoding='utf-8')
        (work / 'build/captions.srt').write_text('1\n00:00:00,000 --> 00:00:02,000\n测试字幕\n', encoding='utf-8')
        manifest = {
            'fps': 30, 'workers': 2, 'audio': 'build/audio.wav', 'subtitles': 'build/captions.ass',
            'clips': [{'html': 'scenes/001.html', 'frames': 30}, {'html': 'scenes/002.html', 'frames': 30}],
        }
        (work / 'render.json').write_text(json.dumps(manifest), encoding='utf-8')

        # Hard-linked prior delivery must not be overwritten in place.
        delivery = self.p / 'final_video.mp4'
        delivery.write_bytes(b'old video bytes')
        preserved = self.p / 'preserved_hardlink.mp4'
        os.link(delivery, preserved)

        report = run_manifest(work, delivery=delivery)
        self.assertTrue(report['verified'])
        self.assertEqual((report['width'], report['height'], report['fps'], report['frames']), (320, 180, 30, 60))
        self.assertEqual(preserved.read_bytes(), b'old video bytes')
        self.assertNotEqual(delivery.read_bytes(), b'old video bytes')
        self.assertTrue((self.p / 'final_video.srt').is_file())
        self.assertTrue((work / 'build/render_000.log').is_file())
        self.assertTrue((work / 'build/render_001.log').is_file())

        # Updating subtitles only needs compose; rendered clip stays untouched.
        clip_mtime = (work / 'build/clip_000.mp4').stat().st_mtime_ns
        (work / 'build/captions.ass').write_text(ass.replace('第二句', '改后第二句'), encoding='utf-8')
        recomposed = compose(work, delivery=delivery)
        self.assertTrue(recomposed['verified'])
        self.assertEqual((work / 'build/clip_000.mp4').stat().st_mtime_ns, clip_mtime)

        # Without subtitles, compose uses video stream copy.
        self.settings(enable_subtitles=False)
        (work / 'materials.json').write_text(
            json.dumps({**editing.read(work / 'materials.json'), 'subtitles': False}), encoding='utf-8'
        )
        manifest.pop('subtitles')
        (work / 'render.json').write_text(json.dumps(manifest), encoding='utf-8')
        fast = compose(work, delivery=delivery)
        self.assertTrue(fast['verified'])
        self.assertEqual(fast['encoder'], 'copy')

    def test_assembly_helper_rejects_audio_mismatch_and_outside_paths(self):
        sys.path.insert(0, str(ENGINE))
        from core.infra.motion.assembly import run_manifest
        _, work = self.prepare()
        (work / 'scenes').mkdir(exist_ok=True)
        (work / 'build').mkdir(exist_ok=True)
        (work / 'scenes/001.html').write_text('<p>hi</p>', encoding='utf-8')
        ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-ac', '1', '-ar', '48000', work / 'build/audio.wav')

        (work / 'render.json').write_text(json.dumps({
            'fps': 30, 'audio': 'build/audio.wav',
            'clips': [{'html': 'scenes/001.html', 'frames': 30}],  # 1s vs 2s audio
        }), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Audio duration'):
            run_manifest(work)

        (work / 'render.json').write_text(json.dumps({
            'fps': 30, 'audio': 'build/audio.wav',
            'clips': [{'html': '../../../text/raw.json', 'frames': 60}],
        }), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'inside the working directory'):
            run_manifest(work)

if __name__=='__main__':
    unittest.main()
