"""Observable editing contracts, source preservation, timing and real rendering."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import editing
import edit_render
from server import Studio,atomic_json
DESKTOP=Path(__file__).resolve().parents[2];ENGINE=DESKTOP/'build/engine'

class EditingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.studio=Studio(ENGINE,root/'data',root/'projects');self.p=self.studio.workspace/'剪辑 with spaces'
        for d in ['text','images','voice']:(self.p/d).mkdir(parents=True)
        raw={'source_name':'test','content':'第一段。第二段。','total_length':8,'video_titles':['视频'],'cover_titles':['封面'],'cover_subtitles':[],'golden_quotes':[]}
        script={**raw,'actual_segments':2,'segments':[{'index':1,'content':'第一段。','visualizer':'image'},{'index':2,'content':'第二段。','visualizer':'image'}]}
        atomic_json(self.p/'text/raw.json',raw);atomic_json(self.p/'text/script.json',script)
        from PIL import Image
        for i in [1,2]:
            Image.new('RGB',(320,180),(30*i,60,120)).save(self.p/f'images/segment_{i}.png')
            edit_render.ffmpeg('-f','lavfi','-i',f'sine=frequency={400+i*100}:duration=1','-ac','1','-ar','48000',self.p/f'voice/voice_{i}.wav')
        state=self.studio.state(self.p);state['settings']={'global':{'opening_quote':False},'step5':{'assembly_method':'agent','agent_skill':'editorial-explainer','video_size':'320x180','narration_speed_factor':1,'enable_subtitles':True,'bgm_filename':None},'three_by_four':{'enabled':False}}
        self.studio.persist(self.p,state);self.id=self.studio.project_id(self.p)
    def tearDown(self):self.tmp.cleanup()
    def prepare(self,**body):
        launch=self.studio.prepare_agent(self.id,{'step':'5',**body});return launch,Path(launch['cwd'])
    def plan(self,work):return editing.read(work/'plan.json')
    def setplan(self,work,plan):editing.write(work/'plan.json',plan)
    def test_agent_tools_render_review_publish_and_reuse_without_app_render_job(self):
        _,work=self.prepare()
        tool=[sys.executable,str(work/'studio_edit.py')]
        subprocess.run([*tool,'preview','--seconds','1'],check=True,capture_output=True,text=True,timeout=90)
        self.assertTrue((work/'previews/contact-sheet.png').is_file())
        subprocess.run([*tool,'render'],check=True,capture_output=True,text=True,timeout=90)
        self.assertTrue(edit_render.candidate_current(work))
        before=editing.stamp(work/'build/candidate.mp4')
        with patch('edit_render.render_clip',side_effect=AssertionError('Do not render a verified candidate again')):
            edit_render.render(work)
        self.assertEqual(before,editing.stamp(work/'build/candidate.mp4'))
        subprocess.run([*tool,'publish'],check=True,capture_output=True,text=True,timeout=30)
        self.assertTrue((self.p/'final_video.mp4').is_file())
        detail=self.studio.detail(self.id)
        self.assertTrue(detail['assembly_edit']['published'])
        self.assertNotIn('5',detail['state']['stale'])
        detail=self.studio.finish_agent(self.id,{})
        self.assertFalse(detail['warning'])
        self.assertEqual(before,editing.stamp(work/'build/candidate.mp4'))
        plan=self.plan(work);plan['notes']='changed after publishing';self.setplan(work,plan)
        with self.assertRaisesRegex(ValueError,'Render and verify'):
            edit_render.publish(work)
    def test_context_skill_and_provider_contract(self):
        launch,work=self.prepare();context=Path(launch['context']).read_text()
        self.assertIn('editorial-explainer/SKILL.md',context);self.assertTrue((work/'EDITING.md').is_file())
        self.assertEqual([s['index'] for s in self.plan(work)['scenes']],[1,2])
        self.assertIn('估算',context);self.assertIn('python studio_edit.py publish',context)
        self.assertEqual(len([p for p in (self.studio.library_root/'skills/step5').iterdir() if (p/'SKILL.md').is_file()]),3)
        from server import engine_settings
        self.assertEqual(engine_settings({'step5':{'assembly_method':'agent','agent_skill':'x','video_size':'320x180'}}),{'step5':{'video_size':'320x180'}})
    def test_plan_requires_all_scenes_and_canonical_captions(self):
        _,work=self.prepare();plan=self.plan(work);plan['scenes'].pop();self.setplan(work,plan)
        with self.assertRaisesRegex(ValueError,'every narration'):editing.validate(work)
        plan=self.plan(work);plan['scenes'].append({'index':2,'trim':[0,1],'caption_timing':'reviewed','captions':[{'text':'修改文案','start':0,'end':1}]});self.setplan(work,plan)
        with self.assertRaisesRegex(ValueError,'preserve'):editing.validate(work)
    def test_bad_timing_anchor_and_asset_paths_are_rejected(self):
        _,work=self.prepare();original=self.plan(work)
        mutations=[lambda p:p['scenes'][0].update(trim=[1,0]),lambda p:p['scenes'][0].update(pause_before=float('nan')),
                   lambda p:p['scenes'][0].update(html='../missing.html'),
                   lambda p:p['scenes'][0].update(overlays=[{'kind':'keyword','text':'x','at':'不存在'}]),
                   lambda p:p.update(music=[{'track':'../../secret'}])]
        for change in mutations:
            plan=copy.deepcopy(original);change(plan);self.setplan(work,plan)
            with self.assertRaises((ValueError,KeyError)):editing.validate(work)
    def test_speed_pause_and_caption_timing_share_one_clock(self):
        _,work=self.prepare();payload=editing.read(work/'studio-payload.json');payload['settings']['narration_speed_factor']=1.25;editing.write(work/'studio-payload.json',payload)
        plan=self.plan(work);plan['scenes'][0]['pause_before']=.2;self.setplan(work,plan)
        check=editing.validate(work);self.assertEqual(check['scenes'][0]['frames'],30);self.assertEqual(check['scenes'][1]['frames'],24)
        self.assertAlmostEqual(check['duration'],1.8)
    def test_revisions_keep_original_and_source_changes_block_render(self):
        launch,work=self.prepare();before=(work/'plan.json').read_bytes();self.studio.finish_agent(self.id,{})
        newer,newwork=self.prepare(revise=True);self.assertNotEqual(work,newwork);self.assertEqual((work/'plan.json').read_bytes(),before)
        self.studio.finish_agent(self.id,{})
        state=self.studio.state(self.p);state['settings']['step5']['narration_speed_factor']=1.2;self.studio.persist(self.p,state)
        self.assertFalse(self.studio.detail(self.id)['assembly_edit']['current'])
        with self.assertRaisesRegex(ValueError,'sources or settings changed'):self.studio.run_job(self.id,{'step':'5','mode':'edit_render'})
    def test_invalid_plan_releases_agent_without_touching_exports(self):
        previous=self.p/'final_video.mp4';previous.write_bytes(b'previous');_,work=self.prepare();(work/'plan.json').write_text('{broken')
        d=self.studio.finish_agent(self.id,{});self.assertNotIn('agent_active',d['state']);self.assertTrue(d['warning']);self.assertEqual(previous.read_bytes(),b'previous')
    def test_html_and_plan_save_refresh_detail_without_invalidating_other_steps(self):
        _,work=self.prepare();before=self.studio.detail(self.id)['revision'];plan=self.plan(work);plan['notes']='revision';self.setplan(work,plan)
        after=self.studio.detail(self.id);self.assertNotEqual(before,after['revision']);self.assertNotIn('6',after['state']['stale'])
    def test_real_preview_and_master_preserve_inputs_and_exact_frames(self):
        _,work=self.prepare();plan=self.plan(work);plan['scenes'][0]['overlays']=[{'kind':'keyword','text':'第一段','at':'第一段','end':.8}];plan['sfx']=[{'sound':'click','at':.15,'volume':.1}];self.setplan(work,plan)
        before={str(p):p.read_bytes() for p in [self.p/'text/raw.json',self.p/'text/script.json',self.p/'voice/voice_1.wav',self.p/'images/segment_1.png']}
        output,report=edit_render.render(work,preview=True,seconds=2)
        self.assertEqual(report['frames'],60);self.assertTrue((work/'previews/contact-sheet.png').is_file());self.assertTrue(report['warnings'])
        meta=edit_render.probe(output);self.assertEqual(next(s for s in meta['streams'] if s['codec_type']=='video')['nb_frames'],'60')
        for name,value in before.items():self.assertEqual(Path(name).read_bytes(),value)

    def test_real_chunk_seams_keep_frames_and_visuals(self):
        from PIL import Image
        import io
        _,work=self.prepare()
        with patch('edit_render.CHUNK_FRAMES',30):
            output,report=edit_render.render(work,preview=True,seconds=2)
        self.assertEqual(report['chunks'],2);self.assertEqual(report['frames'],60)
        ffmpeg=os.environ['AIGC_FFMPEG_PATH']
        for frame,expected in [(0,(30,60,120)),(29,(30,60,120)),(30,(60,60,120)),(59,(60,60,120))]:
            result=subprocess.run([ffmpeg,'-v','error','-i',str(output),'-vf',f'select=eq(n\\,{frame})','-frames:v','1','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True)
            with Image.open(io.BytesIO(result.stdout)) as image:
                rgb=image.convert('RGB').getpixel((3,3))
                self.assertTrue(all(abs(a-b)<12 for a,b in zip(rgb,expected)),(frame,rgb,expected))
    def test_real_job_publishes_landscape_portrait_captions_and_report(self):
        import time
        state=self.studio.state(self.p);state['settings']['three_by_four']['enabled']=True;self.studio.persist(self.p,state)
        _,work=self.prepare();self.studio.finish_agent(self.id,{})
        job=self.studio.run_job(self.id,{'step':'5','mode':'edit_render'})
        deadline=time.monotonic()+90
        while self.studio.jobs[job['id']]['status']=='running' and time.monotonic()<deadline:time.sleep(.1)
        job=self.studio.public_job(self.studio.jobs[job['id']])
        self.assertEqual(job['status'],'succeeded',json.dumps(job,ensure_ascii=False)[-5000:])
        for name in ['final_video.mp4','final_video_3x4.mp4','final_video.srt','final_video.qa.json']:self.assertTrue((self.p/name).is_file(),name)
        report=editing.read(self.p/'final_video.qa.json');self.assertEqual(report['frames'],60)
        self.assertNotIn('5',self.studio.detail(self.id)['state']['stale'])
    def test_render_failure_keeps_previous_exports(self):
        import runner
        _,work=self.prepare();edit=self.studio.state(self.p)['assembly_edit'];self.studio.finish_agent(self.id,{})
        existing={name:('old '+name).encode() for name in ['final_video.mp4','final_video_3x4.mp4','final_video.srt']}
        for name,content in existing.items():(self.p/name).write_bytes(content)
        previous=Path.cwd()
        try:
            with patch('edit_render.render',side_effect=ValueError('deliberate renderer failure')):
                with self.assertRaisesRegex(ValueError,'deliberate'):
                    runner.run({'engine':str(ENGINE),'project':str(self.p),'step':'5','mode':'edit_render','settings':self.studio.state(self.p)['settings'],'assembly_edit':edit})
        finally:os.chdir(previous)
        for name,content in existing.items():self.assertEqual((self.p/name).read_bytes(),content)

    def test_custom_html_motion_clip_and_opening_render_without_changing_sources(self):
        state=self.studio.state(self.p);state['settings']['global']['opening_quote']=True;self.studio.persist(self.p,state)
        raw=editing.read(self.p/'text/raw.json');raw['golden_quotes']=['开场。'];atomic_json(self.p/'text/raw.json',raw)
        shutil=__import__('shutil');shutil.copy2(self.p/'voice/voice_1.wav',self.p/'voice/opening.wav');shutil.copy2(self.p/'images/segment_1.png',self.p/'images/opening.png')
        edit_render.ffmpeg('-f','lavfi','-i','testsrc2=s=320x180:r=30:d=1','-c:v','libx264','-pix_fmt','yuv420p',self.p/'images/segment_2.mp4')
        script=editing.read(self.p/'text/script.json');script['segments'][1]['visualizer']='hf';atomic_json(self.p/'text/script.json',script)
        _,work=self.prepare();plan=self.plan(work);self.assertEqual([s['index'] for s in plan['scenes']],[0,1,2])
        html='<!doctype html><script src="build/gsap.min.js"></script><style>html,body{margin:0}#root{background:#2b6155;width:320px;height:180px;position:relative}.box{position:absolute;width:50px;height:50px;background:#f0c060;top:60px;left:30px}</style><div id="root" data-composition-id="main" data-start="0" data-width="320" data-height="180" data-duration="1"><div class="box"></div></div><script>const tl=gsap.timeline({paused:true});tl.to(".box",{x:150,duration:1,ease:"none"});window.__timelines={main:tl};</script>'
        (work/'custom.html').write_text(html);plan['scenes'][1]['html']='custom.html';self.setplan(work,plan)
        output,report=edit_render.render(work,preview=True,seconds=3)
        self.assertEqual(report['frames'],90);self.assertTrue(output.is_file())
        self.assertEqual((work/'custom.html').read_text(),html)
    def test_both_agent_environments_use_writings_model(self):
        state=self.studio.state(self.p);state['settings']['step1']={'llm_server':'deepseek','llm_model':'deepseek-chat'};self.studio.persist(self.p,state)
        code="from pathlib import Path;from agent_env import resolve;import sys;a=resolve(Path(sys.argv[1]),Path(sys.argv[2]),'1');b=resolve(Path(sys.argv[1]),Path(sys.argv[2]),'5');assert a==b;assert b['ANTHROPIC_AUTH_TOKEN']=='editing-test-key'"
        result=subprocess.run([sys.executable,'-c',code,str(ENGINE),str(self.p)],cwd=DESKTOP/'backend',env={**os.environ,'DEEPSEEK_API_KEY':'editing-test-key'},capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,'Assembly and Writing must resolve the same provider environment')

    def test_abandoned_unchanged_session_keeps_review_and_does_not_request_render(self):
        state=self.studio.state(self.p);state['reviewed']['5']='previous';self.studio.persist(self.p,state)
        _,work=self.prepare();self.assertFalse(self.studio.detail(self.id)['assembly_edit']['changed'])
        d=self.studio.finish_agent(self.id,{})
        self.assertEqual(d['state']['reviewed']['5'],'previous');self.assertFalse(d['assembly_edit']['changed'])
    def test_cancelling_render_releases_job_and_preserves_export(self):
        import time
        _,work=self.prepare();self.studio.finish_agent(self.id,{})
        previous=self.p/'final_video.mp4';previous.write_bytes(b'kept export')
        job=self.studio.run_job(self.id,{'step':'5','mode':'edit_render'})
        deadline=time.monotonic()+15
        while not self.studio.jobs[job['id']]['log'] and time.monotonic()<deadline:time.sleep(.02)
        self.studio.cancel_job(job['id'])
        deadline=time.monotonic()+15
        while self.studio.jobs[job['id']]['status']=='cancelling' and time.monotonic()<deadline:time.sleep(.02)
        self.assertEqual(self.studio.jobs[job['id']]['status'],'cancelled');self.assertEqual(previous.read_bytes(),b'kept export')

    def test_real_transitions_keep_timing_and_do_not_flash_black(self):
        import io
        from PIL import Image
        _,work=self.prepare();plan=self.plan(work)
        for transition in ['crossfade','wipe_left','slide_right','zoom_in','fade_white']:
            plan['scenes'][1]['transition']=transition;plan['scenes'][1]['transition_duration']=.2;self.setplan(work,plan)
            output,report=edit_render.render(work,preview=True,seconds=2);self.assertEqual(report['frames'],60)
            result=subprocess.run([os.environ['AIGC_FFMPEG_PATH'],'-v','error','-i',str(output),'-vf','select=eq(n\\,30)','-frames:v','1','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True)
            with Image.open(io.BytesIO(result.stdout)) as image:
                self.assertGreater(sum(image.convert('RGB').getpixel((3,3))),90,transition)

    def test_sources_changed_after_preparation_are_not_rendered(self):
        _,work=self.prepare();payload=editing.read(work/'studio-payload.json');plan=self.plan(work)
        (self.p/'images/segment_1.png').write_bytes(b'changed image')
        with self.assertRaisesRegex(ValueError,'media changed'):editing.verify_inputs(payload,plan)

    def test_partial_publication_failure_rolls_back_every_export(self):
        import exports
        original={'final_video.mp4':b'old video','final_video.srt':b'old captions'}
        outputs={}
        for name,data in original.items():
            (self.p/name).write_bytes(data);candidate=self.p/('new-'+name);candidate.write_bytes(b'new');outputs[self.p/name]=candidate
        replace=Path.replace
        def fail_second(source,target):
            if Path(target).name=='final_video.srt' and str(source).endswith('.candidate'):raise OSError('publish failure')
            return replace(source,target)
        with patch.object(Path,'replace',fail_second):
            with self.assertRaisesRegex(OSError,'publish failure'):exports.publish(self.p,outputs)
        for name,data in original.items():self.assertEqual((self.p/name).read_bytes(),data)
    def test_forced_shutdown_journal_restores_previous_export_on_restart(self):
        import exports
        work=self.p/'.studio/exports/interrupted';work.mkdir(parents=True)
        (self.p/'final_video.mp4').write_bytes(b'partially published');(work/'video.previous').write_bytes(b'old video')
        exports.record(work/'journal.json',{'owner':2147483647,'phase':'publishing','entries':[{'target':'final_video.mp4','backup':'video.previous','candidate':'video.candidate'}]})
        self.assertTrue(exports.recover(self.p));self.assertEqual((self.p/'final_video.mp4').read_bytes(),b'old video');self.assertFalse(work.exists())

    def test_frame_alignment_pads_instead_of_cutting_recording_tail(self):
        edit_render.ffmpeg('-f','lavfi','-i','sine=frequency=500:duration=1.001','-ar','48000',self.p/'voice/voice_1.wav')
        _,work=self.prepare();check=editing.validate(work)
        self.assertGreaterEqual(check['scenes'][0]['length'],1.001)
        self.assertEqual(check['scenes'][0]['frames'],31)
    def test_corrupt_recovery_journal_does_not_stop_service_startup(self):
        work=self.p/'.studio/exports/corrupt';work.mkdir(parents=True);(work/'journal.json').write_text('{broken')
        reopened=Studio(ENGINE,self.studio.data,self.studio.workspace)
        self.assertIn('recovery needs review',reopened.state(self.p)['stale']['5']['reason'])
        self.assertTrue((work/'journal.json').exists())

    def test_invalid_saved_plan_can_be_reopened_for_agent_repair(self):
        _,work=self.prepare();(work/'plan.json').write_text('{broken');self.studio.finish_agent(self.id,{})
        launch,revised=self.prepare(revise=True)
        self.assertEqual((revised/'plan.json').read_text(),'{broken')
        self.assertIn(self.studio.library.prompt('step5_editing_agent.md', 'invalid'),Path(launch['context']).read_text());self.assertFalse(self.studio.detail(self.id)['assembly_edit']['valid'])

    def test_music_ducking_lowers_music_relative_to_narration(self):
        import numpy as np
        import wave
        tone=Path(self.tmp.name)/'music.wav'
        edit_render.ffmpeg('-f','lavfi','-i','sine=frequency=1000:duration=2','-ar','48000',tone)
        added=self.studio.import_music({'path':str(tone)})['name']
        _,work=self.prepare();plan=self.plan(work);plan['music']=[{'track':added,'start':0,'volume':.25}];self.setplan(work,plan)
        check=editing.validate(work);edit_render.assets(work,check)
        ratios=[]
        for enabled in [False,True]:
            check['payload']['settings']['audio_ducking_enabled']=enabled
            mixed=edit_render.mix(work,check)
            with wave.open(str(mixed),'rb') as wav:
                samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2').reshape(-1,2)[:,0].astype(float)
            section=samples[24000:40000];frequencies=np.fft.rfftfreq(len(section),1/48000);spectrum=np.abs(np.fft.rfft(section*np.hanning(len(section))))
            energy=lambda frequency:max(spectrum[(frequencies>frequency-8)&(frequencies<frequency+8)])
            ratios.append(energy(1000)/energy(500))
        self.assertLess(ratios[1],ratios[0]*.6)

    def test_malformed_json_shapes_are_reported_without_breaking_project_loading(self):
        _,work=self.prepare();original=self.plan(work)
        bad=[[],{'version':1,'style':'editorial-explainer','scenes':[None]}]
        other=copy.deepcopy(original);other['scenes'][0]['captions']=[None];bad.append(other)
        for plan in bad:
            self.setplan(work,plan);d=self.studio.detail(self.id)
            self.assertFalse(d['assembly_edit']['valid']);self.assertTrue(d['assembly_edit']['problem'])

    def test_zoom_and_overlay_continuity_across_a_mid_scene_chunk(self):
        import io
        from PIL import Image,ImageDraw
        image=Image.new('RGB',(320,180),(30,60,120));ImageDraw.Draw(image).rectangle((40,0,50,179),fill=(255,255,255));image.save(self.p/'images/segment_1.png')
        _,work=self.prepare();plan=self.plan(work);plan['scenes'][0]['zoom']=1.2;plan['scenes'][0]['overlays']=[{'kind':'keyword','text':'第一段','start':.1,'end':.9}];self.setplan(work,plan)
        with patch('edit_render.CHUNK_FRAMES',15):output,report=edit_render.render(work,preview=True,seconds=1)
        self.assertEqual(report['chunks'],2);frames={}
        for frame in [0,14,15,29]:
            result=subprocess.run([os.environ['AIGC_FFMPEG_PATH'],'-v','error','-i',str(output),'-vf',f'select=eq(n\\,{frame})','-frames:v','1','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True)
            frames[frame]=Image.open(io.BytesIO(result.stdout)).convert('RGB')
        self.assertGreater(sum(frames[29].getpixel((20,3))),600)
        self.assertLess(sum(frames[0].getpixel((20,3))),300)
        for frame in [14,15]:
            self.assertLess(sum(frames[frame].getpixel((200,60))),150)
            self.assertLess(sum(frames[frame].getpixel((200,160))),150)
