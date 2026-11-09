"""Regression checks for lyric provenance, weighting and email rendering."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import lyrics_analysis as lyrics
import weekly_upload_job as job


class LyricsReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'cache.json'
        self.songs = [dict(name='First <song>', artist_name='Artist & One', plays=3,
                           minutes=10.0, track_popularity=50, active_days=2),
                      dict(name='Second', artist_name='Other', plays=1,
                           minutes=3.0, track_popularity=40, active_days=1)]
        self.entries = [self.entry(song) for song in self.songs]

    def entry(self, song):
        return dict(name=song['name'], artist_name=song['artist_name'],
                    status='analyzed', summary=' '.join(['Interpretation'] * 45),
                    themes=['reconnection', 'reconnection'], tones=['hopeful'],
                    version_verified=True, prompt_version=lyrics.PROMPT_VERSION,
                    sources=[dict(label='Lyrics <source>', url='https://example.org/song?a=1&b=2',
                                  kind='lyrics', completeness='complete', accessed_on='2026-11-09')])

    def report(self, entries=None, **extra):
        self.path.write_text(json.dumps(dict(entries=self.entries if entries is None else entries, **extra)))
        return lyrics.build_report(self.songs, '2026-11-01', self.path)

    def test_weighting_deduplicates_tags_and_excludes_missing(self):
        self.entries[1].update(status='unavailable', summary='', themes=[])
        report = self.report()
        self.assertEqual(report['coverage']['analyzed_plays'], 3)
        self.assertEqual(report['theme_prevalence'][0]['plays'], 3)
        self.assertEqual(report['theme_prevalence'][0]['share'], 1)
        self.assertEqual(report['coverage']['total_plays'], 4)

    def test_partial_does_not_enter_denominator(self):
        self.entries[1].update(status='partial', version_verified=False)
        self.entries[1]['sources'][0]['completeness'] = 'partial'
        report = self.report()
        self.assertEqual(report['tracks'][1]['status'], 'partial')
        self.assertEqual(report['coverage']['analyzed_plays'], 3)
        self.assertTrue(report['tracks'][1]['summary'])

    def test_unverified_complete_or_old_prompt_is_not_analyzed(self):
        for changes in ({'version_verified': False}, {'prompt_version': 'old'},
                        {'summary': 'too short'}):
            entries = copy.deepcopy(self.entries)
            entries[0].update(changes)
            self.assertEqual(self.report(entries)['tracks'][0]['status'], 'unavailable')

    def test_instrumental_needs_evidence_and_has_no_themes(self):
        self.entries[1].update(status='instrumental', summary='')
        report = self.report()
        self.assertEqual(report['tracks'][1]['status'], 'instrumental')
        self.assertEqual(report['tracks'][1]['themes'], [])
        self.assertEqual(report['coverage']['analyzed_plays'], 3)
        self.entries[1]['sources'] = []
        self.assertEqual(self.report()['tracks'][1]['status'], 'unavailable')

    def test_version_and_artist_mismatch_never_fuzzy_match(self):
        for name, artist in [('Second (Remix)', 'Other'), ('Second', 'Cover Artist')]:
            entries = copy.deepcopy(self.entries)
            entries[1].update(name=name, artist_name=artist)
            self.assertEqual(self.report(entries)['tracks'][1]['status'], 'unavailable')

    def test_no_overlap_and_meaningful_overlap(self):
        self.assertIn('Shared themes:', self.report()['synthesis'])
        self.entries[1]['themes'] = ['independence']
        self.assertIn('No strong shared theme', self.report()['synthesis'])

    def test_bad_cache_missing_cache_and_zero_songs(self):
        self.assertEqual(lyrics.build_report(self.songs, '2026-11-01', self.path)['coverage']['analyzed_songs'], 0)
        for text in ('invalid json', '[]', '{"entries":null}'):
            self.path.write_text(text)
            report = lyrics.build_report(self.songs, '2026-11-01', self.path)
            self.assertTrue(report['warnings'])
            self.assertEqual(len(report['tracks']), 2)
        empty = lyrics.build_report([], '2026-11-01', self.path)
        self.assertEqual(empty['theme_prevalence'], [])
        self.assertIn('0/0', lyrics.render_text(empty))

    def test_duplicate_identity_needs_review(self):
        report = self.report(self.entries + [self.entries[0]])
        self.assertEqual(report['tracks'][0]['status'], 'unavailable')
        self.assertIn('duplicate', report['tracks'][0]['note'])

    def test_raw_lyrics_are_not_exported(self):
        self.entries[0]['lyrics'] = 'RAW_LYRICS_SENTINEL'
        self.entries[0]['sources'][0]['raw_page'] = 'RAW_PAGE_SENTINEL'
        report = self.report()
        self.assertNotIn('RAW_', json.dumps(report))

    def test_html_and_text_escape_and_keep_sources_and_order(self):
        report = self.report()
        html = job.build_html(self.songs, self.songs, [], set(), '2026-11-01', report)
        text = job.build_text(self.songs, self.songs, [], set(), '2026-11-01', report)
        self.assertIn(lyrics.HEADING, html)
        self.assertIn(lyrics.HEADING, text)
        self.assertIn('First &lt;song&gt;', html)
        self.assertNotIn('First <song>', html)
        self.assertIn('a=1&amp;b=2', html)
        self.assertIn('https://example.org/song?a=1&b=2', text)
        for output in (lyrics.render_html(report), lyrics.render_text(report)):
            self.assertLess(output.index('#1'), output.index('#2'))
            self.assertIn('hopeful', output)

    def test_unsafe_source_urls_are_discarded(self):
        for url in ('javascript:alert(1)', '//example.org', 'https://x/\nfoo', 'https://[broken', 'https://user@host/path'):
            self.assertEqual(lyrics.safe_url(url), '')
        self.entries[0]['sources'][0]['url'] = 'javascript:alert(1)'
        report = self.report()
        self.assertNotIn('javascript:', lyrics.render_html(report))
        self.assertEqual(report['tracks'][0]['status'], 'unavailable')

    def test_reviewed_synthesis_invalidates_on_changed_analysis(self):
        report = self.report()
        narrative = {'2026-11-01': dict(analysis_signature=report['analysis_signature'], text='Reviewed comparison.')}
        self.assertEqual(self.report(weekly_syntheses=narrative)['synthesis'], 'Reviewed comparison.')
        self.entries[0]['themes'] = ['a different theme']
        self.assertNotEqual(self.report(weekly_syntheses=narrative)['synthesis'], 'Reviewed comparison.')

    def test_report_only_wins_over_send_and_skip_audio_does_not_separate(self):
        self.report()
        with patch.object(job, 'top_songs', return_value=self.songs), \
             patch.object(job, 'DOWNLOADS_DIR', Path(self.tmp.name)), \
             patch.object(job, 'REPORTS_DIR', Path(self.tmp.name)), \
             patch.object(job.library_db, 'connect') as connect, \
             patch.object(job.analyze_stems, 'analyse_track') as analyze, \
             patch.object(job.analyze_stems, 'send_email') as send, \
             patch('sys.argv', ['weekly_upload_job.py', '--week-of', '2026-11-01',
                                '--lyrics-cache', str(self.path), '--report-only', '--send', '--skip-audio']):
            connect.return_value.execute.return_value.fetchall.return_value = []
            self.assertEqual(job.main(), 0)
            analyze.assert_not_called()
            send.assert_not_called()
        data = json.loads((Path(self.tmp.name) / 'weekly_uploads_2026-11-01.json').read_text())
        self.assertEqual(len(data['lyrics_analysis']['tracks']), 2)
        self.assertTrue((Path(self.tmp.name) / 'weekly_uploads_2026-11-01.txt').exists())


if __name__ == '__main__':
    unittest.main()
