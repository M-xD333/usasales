import unittest
import bot
import sources


class MusicSearch(unittest.TestCase):
    def test_searches_enabled_by_default_with_existing_enable_list(self):
        active = {s.id for s in sources.active_sources({'ENABLE_SOURCES': 'sd-frontpage,sd-popular'})}
        self.assertTrue({'sd-q-drum-machine', 'sd-q-sampler', 'sd-q-synthesizer', 'sd-q-roland-aira'} <= active)

    def test_instruments_recognized_before_computer_keyboard(self):
        for title in ['Roland AIRA Compact S-1 Synthesizer $150', 'Korg Drum Machine $100',
                      'Elektron Digitakt Sampler $500', 'Moog Synthesizer Keyboard $499']:
            with self.subTest(title=title):
                self.assertEqual(bot.classify(title), 'music_gear')
        self.assertEqual(bot.classify('Mechanical gaming keyboard $100'), 'keyboard')
        self.assertEqual(bot.classify('VST software synthesizer $50'), 'other')
        self.assertEqual(bot.classify('Drum machine sample pack $10'), 'other')
        self.assertEqual(bot.classify('Coffee sampler pack $20'), 'other')
