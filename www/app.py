import logging; logging.basicConfig(level=logging.INFO)
import asyncio
import datetime
import json
import os
import time

from aiohttp import web
from jinja2 import Environment, FileSystemLoader
import markdown2

from coroweb import add_routes, add_static
from handlers import cookie2user, COOKIE_NAME
import orm


def init_jinja2(app, **kw):
    logging.info('init jinja2...')
    options = dict(
        autoescape = kw.get('autoescape', True),
        block_start_string = kw.get('block_start_string', '{%'),
        block_end_string = kw.get('block_end_string', '%}'),
        variable_start_string = kw.get('variable_start_string', '{{'),
        variable_end_string = kw.get('variable_end_string', '}}'),
        auto_reload = kw.get('auto_reload', True)
    )
    path = kw.get('path', None)
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    logging.info('set jinja2 template path: %s' % path)
    env = Environment(loader=FileSystemLoader(path), **options)
    filters = kw.get('filters', None)
    if filters is not None:
        for name, f in filters.items():
            env.filters[name] = f
    app['__templating__'] = env


async def logger_factory(app, handler):
    async def logger(request):
        logging.info('Request: %s %s' % (request.method, request.path))
        return (await handler(request))
    return logger


async def auth_factory(app, handler):
    async def auth(request):
        logging.info('check user: %s %s' % (request.method, request.path))
        request.__user__ = None
        cookie_str = request.cookies.get(COOKIE_NAME)
        if cookie_str:
            user = await cookie2user(cookie_str)
            if user:
                logging.info('set current user: %s' % user.email)
                request.__user__ = user
        # if request.path.startswith('/manage/') and (request.__user__ is None or not request.__user__.admin):
        #     return web.HTTPFound('/signin')
        return (await handler(request))
    return auth


async def data_factory(app, handler):
    async def parse_data(request):
        if request.method == 'POST':
            ct = request.content_type.lower()
            if ct.startswith('application/json'):
                request.__data__ = await request.json()
                logging.info('request json: %s' % request.__data__)
            elif ct.startswith('application/x-www-form-urlencoded'):
                request.__data__ = await request.post()
                logging.info('request form: %s' % request.__data__)
        return (await handler(request))
    return parse_data


async def response_factory(app, handler):
    async def response(request):
        logging.info('Response handler...')
        r = await handler(request)
        if isinstance(r, web.StreamResponse):
            return r
        if isinstance(r, bytes):
            return web.Response(body=r, content_type='application/octet-stream')
        if isinstance(r, str):
            if r.startswith('redirect:'):
                return web.HTTPFound(r[9:])
            resp = web.Response(body=r.encode('utf8'))
            resp.content_type = 'text/html;charset=utf-8'
            return resp
        if isinstance(r, dict):
            template = r.get('__template__')
            if template is None:
                s = json.dumps(r, ensure_ascii=False, default=lambda o: o.__dict__)
                return web.Response(body=s.encode('utf8'), content_type='application/json')
            else:
                if 'user' not in r:
                    r['user'] = request.__user__
                s = app['__templating__'].get_template(template).render(**r)
                resp = web.Response(body=s.encode('utf8'))
                resp.content_type = 'text/html;charset=utf-8'
                return resp
        if isinstance(r, int) and r >= 100 and r < 600:
            return web.Response(r)
        if isinstance(r, tuple) and len(r) == 2:
            t, m = r
            if isinstance(t, int) and t >= 100 and t < 600:
                return web.Response(t, str(m))
        # default
        resp = web.Response(body=str(r).encode('utf8'))
        resp.content_type = 'text/plain;charset=utf-8'
        return resp
    return response


def datetime_filter(t):
    delta = int(time.time() - t)
    if delta < 60:
        return '1分钟前'
    if delta < 3600:
        return '%s分钟前' % (delta // 60)
    if delta < 3600 * 24:
        return '%s小时前' % (delta // 3600)
    if delta < 3600 * 24 * 7:
        return '%s天前' % (delta // (3600 * 24))
    dt = datetime.fromtimestamp(t)
    return '%4s-%02s-%02s' % (dt.year, dt.month, dt.day)


def markdown_filter(mtext):
    return markdown2.markdown(mtext)


def text2html_filter(text):
    lines = map(lambda s: '<p>%s</p>' % s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'), filter(lambda s: s.strip() != '', text.split('\n')))
    return ''.join(lines)


def init_app():
    loop = asyncio.get_event_loop()
    db_task = orm.create_pool(loop=loop, user='www-data', password='www-data', db='awesome')
    loop.run_until_complete(db_task)
    app = web.Application(loop=loop, middlewares=[
        logger_factory, auth_factory, response_factory
    ])
    init_jinja2(app, filters=dict(
        datetime=datetime_filter, markdown=markdown_filter, text2html=text2html_filter
    ))
    add_routes(app, 'handlers')
    add_static(app)
    return app



if __name__ == '__main__':
    app = init_app()
    web.run_app(app, host='127.0.0.1', port=8000)
