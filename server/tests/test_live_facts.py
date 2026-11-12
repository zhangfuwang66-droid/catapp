"""冻结时钟与模拟Open-Meteo响应，不访问公网、不使用真实模型Key。"""

import asyncio
from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import AsyncMock

from live_facts import FORECAST_URL, GEOCODING_URL, LiveFacts, SHANGHAI


def weather_result(prompt):
    return json.JSONDecoder().raw_decode(prompt.split('【实时信息·天气】', 1)[1].split('：', 1)[1])[0]


class LiveFactsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = datetime(2026, 11, 12, 12, 34, 56, tzinfo=SHANGHAI).timestamp()
        self.elapsed = 0
        self.calls = []
        self.facts = LiveFacts(fetcher=self.fetch, clock=lambda: self.now, cache_clock=lambda: self.elapsed)

    async def fetch(self, url, params):
        self.calls.append((url, params))
        if url == GEOCODING_URL:
            return {'results': [{'name': params['name'], 'admin1': '测试行政区', 'country_code': 'CN',
                                 'latitude': 39.9, 'longitude': 116.4, 'population': 100000}]}
        self.assertEqual(url, FORECAST_URL)
        now = datetime.fromtimestamp(self.now, SHANGHAI)
        return {'current': {'time': now.strftime('%Y-%m-%dT%H:%M'), 'temperature_2m': 22.5, 'weather_code': 0},
                'daily': {'time': [(now + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(3)],
                          'temperature_2m_min': [10, 11, 12], 'temperature_2m_max': [20, 21, 22],
                          'weather_code': [0, 61, 3], 'precipitation_probability_max': [0, 70, 20]}}

    async def test_beijing_time_date_weekday_and_idle_chat_do_not_call_network(self):
        for question in ('现在北京时间几点？', '今天几号星期几？', '你好', '我喜欢北京的天气', '资料里的天气报销条款是什么？'):
            prompt = await self.facts.context_for(question)
            self.assertIn('2026-11-12', prompt)
            self.assertIn('12:34:56', prompt)
            self.assertIn('星期四', prompt)
            self.assertIn('Asia/Shanghai', prompt)
            self.assertNotIn('【实时信息·天气】', prompt)
        self.assertEqual(self.calls, [])

    async def test_only_explicit_city_is_sent_and_success_has_source_observation(self):
        prompt = await self.facts.context_for('我的手机号是13800000000，帮我查一下北京今天的天气')
        result = weather_result(prompt)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['source'], 'Open-Meteo')
        self.assertEqual(result['temperature_celsius'], 22.5)
        self.assertEqual(result['condition'], '晴')
        self.assertIn('observed_at', result)
        self.assertEqual(self.calls[0], (GEOCODING_URL, {'name': '北京', 'count': 5, 'language': 'zh', 'format': 'json'}))
        self.assertNotIn('13800000000', json.dumps(self.calls))
        self.assertEqual([url for url, _ in self.calls], [GEOCODING_URL, FORECAST_URL])

    async def test_missing_city_multiple_cities_and_unsupported_dates_do_not_guess(self):
        for question, status in (('今天的天气怎么样', 'needs_city'), ('北京和上海天气如何', 'needs_city'),
                                 ('北京下周天气', 'unsupported_date'), ('北京昨天温度多少', 'unsupported_date')):
            result = weather_result(await self.facts.context_for(question))
            self.assertEqual(result['status'], status)
            self.assertNotIn('temperature_celsius', result)
        self.assertEqual(self.calls, [])

    async def test_tomorrow_uses_requested_daily_forecast_instead_of_current_temperature(self):
        result = weather_result(await self.facts.context_for('北京明天天气怎么样'))
        self.assertEqual(result['date'], '2026-11-13')
        self.assertEqual(result['kind'], 'forecast')
        self.assertEqual(result['low_celsius'], 11)
        self.assertEqual(result['high_celsius'], 21)
        self.assertEqual(result['rain_probability'], 70)
        self.assertEqual(result['condition'], '小雨')
        self.assertNotIn('temperature_celsius', result)

    async def test_weather_cache_coalesces_requests_and_expires_without_regeocoding(self):
        results = await asyncio.gather(*(self.facts.context_for('北京现在天气') for _ in range(3)))
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(weather_result(results[0])['cached'])
        self.assertTrue(weather_result(results[1])['cached'])
        self.elapsed += 301
        await self.facts.context_for('北京现在天气')
        self.assertEqual([url for url, _ in self.calls], [GEOCODING_URL, FORECAST_URL, FORECAST_URL])

    async def test_timeout_and_old_observation_do_not_reuse_an_answer_from_previous_turn(self):
        await self.facts.context_for('北京现在天气')
        self.elapsed += 301
        self.facts.fetcher = AsyncMock(side_effect=TimeoutError)
        result = weather_result(await self.facts.context_for('北京现在天气'))
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('temperature_celsius', result)
        self.assertNotIn('【实时信息·天气】', await self.facts.context_for('你好'))

        async def stale(url, params):
            data = await self.fetch(url, params)
            if url == FORECAST_URL:
                data['current']['time'] = '2026-11-11T00:00'
            return data
        self.facts.fetcher = stale
        self.assertEqual(weather_result(await self.facts.context_for('北京现在天气'))['status'], 'unavailable')

    async def test_ambiguous_place_and_invalid_coordinates_never_start_forecast(self):
        async def ambiguous(url, params):
            self.assertEqual(url, GEOCODING_URL)
            return {'results': [dict(name='朝阳', country_code='CN', admin1=region, latitude=40, longitude=120)
                                for region in ('北京', '辽宁')]}
        self.facts.fetcher = ambiguous
        self.assertEqual(weather_result(await self.facts.context_for('朝阳天气怎么样'))['status'], 'ambiguous_city')

        async def invalid(url, params):
            self.assertEqual(url, GEOCODING_URL)
            return {'results': [dict(name='北京', latitude=True, longitude=999)]}
        self.facts.fetcher = invalid
        self.assertEqual(weather_result(await self.facts.context_for('北京天气'))['status'], 'city_not_found')

    async def test_cancellation_does_not_turn_into_weather_or_leave_external_work_running(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def blocking(url, params):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        self.facts.fetcher = blocking
        task = asyncio.create_task(self.facts.context_for('北京天气'))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())


if __name__ == '__main__':
    unittest.main()
