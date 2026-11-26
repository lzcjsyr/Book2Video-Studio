"""Google 图像成本按模型和输出档位累加；未知模型只计数，不猜价格。"""
from pathlib import Path
import sys
import unittest

ENGINE = Path(__file__).resolve().parents[2] / 'build/engine'
if not ENGINE.exists():  # 尚未打包时直接测源码，避免必须先跑 stage
    ENGINE = Path(__file__).resolve().parents[2] / 'engine'
sys.path.insert(0, str(ENGINE))

from core.infra.ai import image_cost
from core.infra.ai.llm_client import _google_thinking_level


class ImageCostTests(unittest.TestCase):
    def setUp(self):
        image_cost.reset_costs()

    def tearDown(self):
        image_cost.reset_costs()

    def test_price_follows_the_resolution_tier(self):
        self.assertAlmostEqual(image_cost.image_price('gemini-nano-banana-2.1', '1K'), 0.0336)
        self.assertAlmostEqual(image_cost.image_price('gemini-nano-banana-2.1', '4K'), 0.113)
        self.assertAlmostEqual(image_cost.image_price('gemini-3-pro-image', '4K'), 0.24)

    def test_publisher_path_and_case_are_normalized(self):
        self.assertAlmostEqual(
            image_cost.image_price('projects/x/locations/global/publishers/google/models/Gemini-3-Pro-Image', '1K'),
            0.134,
        )

    def test_missing_tier_uses_the_highest_known_price(self):
        # Lite 只公布 1K 价，请求 4K 时按已知最高价计，宁可高估。
        self.assertAlmostEqual(image_cost.image_price('gemini-3.1-flash-lite-image', '4K'), 0.0336)

    def test_unknown_model_is_counted_but_not_priced(self):
        self.assertIsNone(image_cost.image_price('some-future-model', '1K'))
        self.assertIsNone(image_cost.record_image_cost(model='some-future-model', image_size='1K'))
        self.assertEqual(image_cost.cost_summary()['count'], 1)
        self.assertEqual(image_cost.cost_summary()['usd'], 0.0)

    def test_generations_accumulate_per_model(self):
        image_cost.record_image_cost(model='gemini-nano-banana-2.1', image_size='1K')
        image_cost.record_image_cost(model='gemini-nano-banana-2.1', image_size='1K')
        image_cost.record_image_cost(model='gemini-3-pro-image', image_size='4K')
        summary = image_cost.cost_summary()
        self.assertEqual(summary['count'], 3)
        self.assertAlmostEqual(summary['usd'], 0.0336 * 2 + 0.24, places=4)
        self.assertEqual(summary['by_model']['gemini-3-pro-image']['count'], 1)


class ThinkingLevelTests(unittest.TestCase):
    """不同 google-genai 版本的枚举不同，不能硬编码 MINIMAL。"""

    class Level:
        def __init__(self, name):
            self.name = name

    def types_with(self, names):
        return type('Types', (), {'ThinkingLevel': [self.Level(name) for name in names]})

    def test_prefers_minimal_when_supported(self):
        self.assertEqual(_google_thinking_level(self.types_with(['MINIMAL', 'LOW', 'HIGH'])), 'MINIMAL')

    def test_falls_back_to_low_on_older_sdks(self):
        self.assertEqual(_google_thinking_level(self.types_with(['LOW', 'HIGH'])), 'LOW')

    def test_returns_none_when_no_level_is_usable(self):
        self.assertIsNone(_google_thinking_level(self.types_with(['HIGH'])))


if __name__ == '__main__':
    unittest.main()
