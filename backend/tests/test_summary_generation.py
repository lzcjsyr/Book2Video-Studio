"""The summary uses chat completions; Claude Code uses a separate Anthropic endpoint."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ENGINE = Path(__file__).resolve().parents[2] / 'build/engine'


class SummaryGenerationTests(unittest.TestCase):
    def test_mimo_summary_uses_chat_completions_and_keeps_agent_endpoint_separate(self):
        sys.path.insert(0, str(ENGINE))
        import httpx
        import openai
        from core.config import config, get_generation_params
        from core.infra.ai import llm_client
        from core.infra.ai.claude_agent import build_step1_agent_env, build_step4_motion_agent_env
        from core.pipeline.steps import run_step_2

        received = []
        def respond(request):
            received.append((str(request.url), request.headers.get('authorization'), json.loads(request.content)))
            return httpx.Response(200, json={'id':'summary-fixture', 'object':'chat.completion',
                'choices':[{'index':0, 'message':{'role':'assistant', 'content':json.dumps({'summary':'这是一段用于验证正确 API 路由的内容概要。'}, ensure_ascii=False)}, 'finish_reason':'stop'}]})
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary); (project/'text').mkdir()
            (project/'text/raw.json').write_text(json.dumps({'content':'本项目讲述测试材料的主要内容。'}))
            (project/'text/script.json').write_text(json.dumps({'segments':[{'index':1,'content':'本项目讲述测试材料的主要内容。'}]}))
            configuration = project/'test.yaml'
            configuration.write_text('step2:\n  llm_server: mimo\n  llm_model: mimo-v2.6-pro\n')
            params = get_generation_params(configuration)
            self.assertEqual(params['llm_base_url_step2'], 'https://api.xiaomimimo.com/v1')
            with httpx.Client(transport=httpx.MockTransport(respond)) as transport, \
                 patch.object(config, 'MIMO_API_KEY', 'fixture-mimo-key'), \
                 patch.object(config, 'LLM_SERVER_STEP1', 'mimo'), \
                 patch.object(config, 'LLM_MODEL_STEP1', 'mimo-v2.6-pro'), \
                 patch.object(llm_client, 'OpenAI', side_effect=lambda **kwargs: openai.OpenAI(**kwargs, http_client=transport)):
                result = run_step_2(params['llm_server_step2'], params['llm_model_step2'], params['llm_base_url_step2'], str(project))
                self.assertEqual(build_step1_agent_env()['ANTHROPIC_BASE_URL'], 'https://api.xiaomimimo.com/anthropic')
                self.assertEqual(build_step4_motion_agent_env(llm_server='mimo', llm_model='mimo-v2.6-pro',
                    llm_base_url=params['llm_base_url_step2'])['ANTHROPIC_BASE_URL'], 'https://api.xiaomimimo.com/anthropic')
            self.assertTrue(result['success'])
            self.assertTrue(json.loads((project/'text/mini_summary.json').read_text())['summary'])
            self.assertEqual(received[0][0], 'https://api.xiaomimimo.com/v1/chat/completions')
            self.assertEqual(received[0][1], 'Bearer fixture-mimo-key')
            self.assertEqual(received[0][2]['model'], 'mimo-v2.6-pro')
