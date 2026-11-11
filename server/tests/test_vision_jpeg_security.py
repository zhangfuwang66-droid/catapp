"""视觉图片只解码 JPEG；真实小样本与伪造头信息均不访问模型或硬件。"""

import base64
import io
import unittest
from unittest.mock import MagicMock, patch

from PIL import BmpImagePlugin, Image, PngImagePlugin

import support  # 隔离运行时导入，不读取实际配置和密钥。
import device_runtime as runtime


class VisionJpegSecurityTests(unittest.TestCase):
    @staticmethod
    def image_bytes(format='JPEG', *, mode='RGB', size=(24, 18)):
        output = io.BytesIO()
        with Image.new(mode, size) as image:
            image.save(output, format=format)
        return output.getvalue()

    def test_real_jpeg_keeps_rgb_thumbnail_and_jpeg_output(self):
        for mode in ('RGB', 'L'):
            with self.subTest(mode=mode):
                original = self.image_bytes(mode=mode, size=(640, 320))
                with patch.object(runtime.Image, 'open', wraps=Image.open) as open_image:
                    encoded = runtime.prepare_ai_vision_image_b64(original)
                self.assertTrue(encoded)
                self.assertEqual(open_image.call_args.kwargs, {'formats': ['JPEG']})
                with Image.open(io.BytesIO(base64.b64decode(encoded)), formats=['JPEG']) as result:
                    result.load()
                    self.assertEqual(result.size, (320, 160))
                    self.assertEqual(result.mode, 'RGB')
                    self.assertEqual(result.format, 'JPEG')

    def test_real_bmp_and_png_never_run_their_convert_or_load(self):
        for format, plugin in (('BMP', BmpImagePlugin.BmpImageFile), ('PNG', PngImagePlugin.PngImageFile)):
            original = self.image_bytes(format)
            with self.subTest(format=format), \
                    patch.object(plugin, 'convert', side_effect=AssertionError('non-JPEG convert forbidden')) as convert, \
                    patch.object(plugin, 'load', side_effect=AssertionError('non-JPEG load forbidden')) as load:
                self.assertEqual(runtime.prepare_ai_vision_image_b64(original), '')
                convert.assert_not_called()
                load.assert_not_called()

    def test_empty_damaged_and_truncated_jpeg_return_empty_without_raw_fallback(self):
        original = self.image_bytes()
        for damaged in (b'', b'not an image', b'\xff\xd8\xffbroken-jpeg\xff\xd9', original[:40], original[:-20]):
            with self.subTest(length=len(damaged)):
                self.assertEqual(runtime.prepare_ai_vision_image_b64(damaged), '')

    def test_non_positive_or_excessive_dimensions_are_rejected_before_convert_or_load(self):
        for size in ((0, 10), (10, 0), (-1, 10), (10, -1), (4097, 4096), (4096, 4097)):
            source = MagicMock()
            source.__enter__.return_value = source
            source.size = size
            with self.subTest(size=size), patch.object(runtime.Image, 'open', return_value=source) as open_image:
                self.assertEqual(runtime.prepare_ai_vision_image_b64(b'header-only-test'), '')
                self.assertEqual(open_image.call_args.kwargs, {'formats': ['JPEG']})
                source.convert.assert_not_called()
                source.load.assert_not_called()

    def test_pixel_limit_is_inclusive_and_checks_area_without_allocating_large_image(self):
        for size in ((4096, 4096), (8192, 2048)):
            source = MagicMock()
            source.__enter__.return_value = source
            source.size = size
            # 只有头尺寸使用 mock；实际处理始终为 2x2 图片，不构造大图。
            with Image.new('RGB', (2, 2)) as tiny:
                source.convert.return_value = tiny
                with self.subTest(size=size), patch.object(runtime.Image, 'open', return_value=source):
                    self.assertTrue(runtime.prepare_ai_vision_image_b64(b'header-only-test'))
                    source.convert.assert_called_once_with('RGB')

    def test_convert_or_encode_failure_never_returns_original_frame(self):
        original = self.image_bytes()
        for stage in ('convert', 'save'):
            source = MagicMock()
            source.__enter__.return_value = source
            source.size = (24, 18)
            converted = source.convert.return_value.__enter__.return_value
            if stage == 'convert':
                source.convert.side_effect = OSError('simulated decoder failure')
            else:
                converted.save.side_effect = OSError('simulated encoder failure')
            with self.subTest(stage=stage), patch.object(runtime.Image, 'open', return_value=source):
                self.assertEqual(runtime.prepare_ai_vision_image_b64(original), '')

    def test_optional_pillow_fallback_only_passes_complete_jpeg_markers(self):
        original = self.image_bytes()
        with patch.object(runtime, 'PIL_AVAILABLE', False), \
                patch.object(runtime.Image, 'open', side_effect=AssertionError('fallback must not decode')) as open_image:
            self.assertEqual(runtime.prepare_ai_vision_image_b64(original), base64.b64encode(original).decode('ascii'))
            for other in (b'', b'arbitrary bytes', self.image_bytes('BMP'), self.image_bytes('PNG'),
                          original[2:], original[:-2]):
                self.assertEqual(runtime.prepare_ai_vision_image_b64(other), '')
            open_image.assert_not_called()


if __name__ == '__main__':
    unittest.main()
