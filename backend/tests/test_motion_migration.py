import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from library import install_bundled
from server import migrate_state


class MotionMigrationTests(unittest.TestCase):
    def test_old_options_preserve_values_and_new_options_win(self):
        state = {'layout':999, 'settings':{'hyperframes_opening':{'ip_name':'My channel'},
            'step4':{'hyperframes_style_preset':'custom', 'hyperframes_render_fps':60,
                     'motion_render_fps':30,'visual_mode':'hyperframes_agent'}}}
        settings = migrate_state(state)['settings']
        self.assertEqual(settings['motion_opening'], {'ip_name':'My channel'})
        self.assertEqual(settings['step4'], {'motion_style_preset':'custom','motion_render_fps':30,'visual_mode':'motion_agent'})
        self.assertEqual(migrate_state(state), state)

    def test_empty_settings_remain_empty(self):
        self.assertEqual(migrate_state({'layout':999,'settings':{}})['settings'], {})

    def test_old_skills_and_custom_styles_are_preserved_outside_visuals(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundled = Path(temporary)/'bundle'; root = Path(temporary)/'library'
            for base in (bundled, root): (base/'prompts').mkdir(parents=True)
            (bundled/'prompts/step4_motion_agent.md').write_text('New browser contract')
            (bundled/'prompts/step4_studio.md').write_text('New studio contract')
            (bundled/'prompts/step4_motion_styles.yaml').write_text('default: {label: Default}')
            old = root/'skills/step4/hyperframes-core'; old.mkdir(parents=True)
            (old/'SKILL.md').write_text('Personal framework notes')
            (root/'prompts/step4_studio.md').write_text('Custom HyperFrames instructions')
            (root/'prompts/step4_hyperframes_styles.yaml').write_text('custom: {label: My style, palette: monochrome, references: []}')
            install_bundled(bundled, root)
            self.assertFalse(old.exists())
            self.assertEqual((root/'.legacy-renderer/skills/step5/hyperframes-core/SKILL.md').read_text(), 'Personal framework notes')
            self.assertEqual((root/'.legacy-step4/step4_studio.md').read_text(), 'Custom HyperFrames instructions')
            self.assertEqual((root/'prompts/step4_studio.md').read_text(), 'New studio contract')
            self.assertIn('My style', (root/'prompts/step4_motion_styles.yaml').read_text())
            install_bundled(bundled, root)
            self.assertIn('My style', (root/'prompts/step4_motion_styles.yaml').read_text())
