"""Image providers publish the requested dimensions even when their accepted sizes differ."""
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ENGINE = Path(__file__).resolve().parents[2] / 'build/engine'
sys.path.insert(0, str(ENGINE))
from core.config import config
from core.infra.ai import image_providers
from core.infra.ai.image_providers import DoubaoImageProvider, GoogleImageProvider, seedream_request_size

SEEDREAM_5 = 'doubao-seedream-5-0-pro-260628'


class SeedreamSizeTests(unittest.TestCase):
    def test_cover_presets_fit_the_limit_and_keep_their_shape(self):
        for size in ['3200x1800', '1800x3200', '3000x2250', '2250x3000', '2500x2500']:
            width, height = map(int, size.split('x'))
            fitted = seedream_request_size(size, SEEDREAM_5)
            w, h = map(int, fitted.split('x'))
            self.assertLessEqual(w * h, config.SEEDREAM_V5_MAX_PIXELS, size)
            self.assertGreaterEqual(w * h, config.SEEDREAM_V5_MIN_PIXELS, size)
            self.assertAlmostEqual(w / h, width / height, delta=0.02)
        # Sizes within the limit, and other models, are asked for as they are.
        self.assertEqual(seedream_request_size('2560x1440', SEEDREAM_5), '2560x1440')
        self.assertEqual(seedream_request_size('3200x1800', 'doubao-seedream-4-0-250828'), '3200x1800')

    def test_seedream_5_5_accepts_fhd_and_downscales_large_requests(self):
        model = 'doubao-seedream-5-5-261028'
        self.assertTrue(config.validate_image_size('1920x1080', model))
        self.assertFalse(config.validate_image_size('640x360', model))
        self.assertEqual(seedream_request_size('1920x1080', model), '1920x1080')
        width, height = map(int, seedream_request_size('3200x1800', model).split('x'))
        self.assertLessEqual(width * height, config.SEEDREAM_V5_MAX_PIXELS)

    def test_a_smaller_request_is_delivered_at_the_size_asked_for(self):
        from PIL import Image
        asked = []
        def generate(prompt, size, model):
            asked.append(size)
            return 'https://example.invalid/cover.png'
        class Response:
            def __init__(self):
                buffer = io.BytesIO()
                Image.new('RGB', tuple(map(int, asked[-1].split('x'))), (200, 120, 60)).save(buffer, format='PNG')
                self.content = buffer.getvalue()
            def raise_for_status(self): pass
        with patch.object(image_providers, 'text_to_image_doubao', side_effect=generate), \
             patch.object(image_providers.requests, 'get', side_effect=lambda *a, **k: Response()):
            result = DoubaoImageProvider().generate(prompt='封面', size='3200x1800', model=SEEDREAM_5)
            self.assertEqual(asked, ['2864x1600'])
            self.assertEqual(result['type'], 'bytes')
            self.assertEqual(Image.open(io.BytesIO(result['data'])).size, (3200, 1800))
            # Within the limit, the provider's URL is used directly.
            self.assertEqual(DoubaoImageProvider().generate(prompt='图', size='2560x1440', model=SEEDREAM_5),
                             {'type': 'url', 'data': 'https://example.invalid/cover.png'})


class GoogleSizeTests(unittest.TestCase):
    def test_google_raster_is_normalized_to_project_dimensions(self):
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (1376, 768)).save(buffer, format="PNG")
        response = {"type": "bytes", "data": buffer.getvalue()}
        with patch.object(image_providers, "text_to_image_google", return_value=response) as generate:
            provider = GoogleImageProvider(use_adc=True)
            result = provider.generate(prompt="Test cover", size="1920x1080", model="gemini-3.1-flash-image")
            generate.assert_called_once_with(prompt="Test cover", size="1920x1080", model="gemini-3.1-flash-image", use_adc=True)
            with Image.open(io.BytesIO(result["data"])) as image:
                self.assertEqual(image.size, (1920, 1080))
            self.assertIs(provider.generate(prompt="Exact size", size="1376x768", model="gemini-3.1-flash-image"), response)


if __name__ == '__main__': unittest.main()
