"""An image service that refuses a prompt: the reason is reported, the prompt reworded, and nothing is retried in vain."""
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ENGINE = Path(__file__).resolve().parents[2] / 'build/engine'
sys.path.insert(0, str(ENGINE))
from core.shared import ContentBlocked
from core.infra.ai import image_client, llm_client


def answer(feedback=None, candidates=()):
    return types.SimpleNamespace(prompt_feedback=feedback, candidates=list(candidates))


def candidate(reason, text=None):
    part = types.SimpleNamespace(text=text, inline_data=None)
    return types.SimpleNamespace(finish_reason=types.SimpleNamespace(name=reason), content=types.SimpleNamespace(parts=[part]))


class GoogleRefusalTests(unittest.TestCase):
    def ask(self, response):
        calls = []
        def generate_content(**kwargs):
            calls.append(kwargs)
            return response
        client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate_content))
        with patch('google.genai.Client', return_value=client), patch.object(llm_client.config, 'GOOGLE_CLOUD_API_KEY', 'fixture-key'):
            try:
                llm_client.text_to_image_google('prompt', '1280x720')
            except Exception as error:
                return error, len(calls)
        self.fail('An answer without an image must raise.')

    def test_a_blocked_prompt_says_why_and_is_asked_once(self):
        """Regression: a refused prompt was reported as "no image data" and sent 30 times."""
        blocked = types.SimpleNamespace(block_reason=types.SimpleNamespace(name='PROHIBITED_CONTENT'),
                                        block_reason_message='This prompt contains sensitive words.')
        error, calls = self.ask(answer(blocked))
        self.assertIsInstance(error, ContentBlocked)
        self.assertIn('PROHIBITED_CONTENT', str(error))
        self.assertIn('sensitive words', str(error))
        self.assertEqual(calls, 1)

    def test_a_blocked_image_is_a_refusal_and_a_text_answer_is_retried(self):
        error, calls = self.ask(answer(candidates=[candidate('IMAGE_SAFETY')]))
        self.assertIsInstance(error, ContentBlocked)
        self.assertEqual(calls, 1)
        with patch('core.shared.time.sleep'):
            error, calls = self.ask(answer(candidates=[candidate('STOP', 'Which scene do you mean?')]))
        self.assertNotIsInstance(error, ContentBlocked)
        self.assertIn('Which scene do you mean?', str(error))
        self.assertGreater(calls, 1)


class SegmentImageTests(unittest.TestCase):
    def generate(self, request, safety=None, rewrite=None):
        with tempfile.TemporaryDirectory() as folder, patch.object(image_client, '_request_image_result', side_effect=request), \
                patch.object(image_client, '_desensitize_image_prompt', return_value=rewrite), patch('core.infra.ai.image_client.time.sleep'):
            return image_client._generate_single_image((2, '年轻人，性感', 'model', '1280x720', folder, 'google', safety or {}))

    def test_a_refused_prompt_is_reworded_and_made(self):
        prompts = []
        def request(server, prompt, size, model):
            prompts.append(prompt)
            if '性感' in prompt:
                raise ContentBlocked('Google 内容审核拦截了提示词（PROHIBITED_CONTENT）')
            return {'type': 'bytes', 'data': b'png'}
        result = self.generate(request, {'max_attempts': 3}, rewrite='年轻人，衣着得体')
        self.assertTrue(result['success'])
        self.assertEqual(prompts, ['年轻人，性感', '年轻人，衣着得体'])

    def test_a_refusal_that_cannot_be_reworded_is_not_repeated_and_says_why(self):
        calls = []
        def request(*args):
            calls.append(args)
            raise ContentBlocked('Google 内容审核拦截了提示词（PROHIBITED_CONTENT）')
        result = self.generate(request)
        self.assertEqual((result['success'], len(calls)), (False, 1))
        self.assertIn('PROHIBITED_CONTENT', result['error'])

    def test_the_step_reports_why_a_segment_failed(self):
        failed = {'success': False, 'segment_index': 2, 'image_path': '', 'error': '内容审核拦截'}
        script = {'segments': [{'index': 1, 'content': '一'}, {'index': 2, 'content': '二'}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(image_client, '_generate_single_image', return_value=failed):
            result = image_client.generate_images_for_segments('google', 'model', script, 'missing', '1280x720', folder,
                                                               description_data={'summary': '小结'}, target_segments=[2])
        self.assertEqual((result['failed_segments'], result['errors']), ([2], {2: '内容审核拦截'}))

    def test_the_style_drops_its_font_clause_and_keeps_the_sentence_break(self):
        with patch.dict(image_client.IMAGE_STYLE_PRESETS, {'fixture': '写实风格；字体为宋体。如果是近景，人物清晰。'}):
            self.assertEqual(image_client.image_style_text('fixture'), '写实风格。如果是近景，人物清晰。')


if __name__ == '__main__':
    unittest.main()
