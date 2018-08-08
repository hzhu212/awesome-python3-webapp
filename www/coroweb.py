import logging
import asyncio
import functools
import importlib
import inspect
import os
from urllib import parse

from aiohttp import web

from apis import APIError


def get(path):
    """define @get('/path') decorator"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kw):
            return func(*args, **kw)
        wrapper.__method__ = 'GET'
        wrapper.__route__ = path
        return wrapper
    return decorator


def post(path):
    """define @post('/path') decorator"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kw):
            return func(*args, **kw)
        wrapper.__method__ = 'POST'
        wrapper.__route__ = path
        return wrapper
    return decorator


def get_required_kw_args(fn):
    args = []
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY and param.default == inspect.Parameter.empty:
            args.append(name)
    return tuple(args)


def get_named_kw_args(fn):
    args = []
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            args.append(name)
    return tuple(args)


def has_named_kw_args(fn):
    params = inspect.signature(fn).parameters
    for param in params.values():
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            return True
    return False


def has_var_kw_arg(fn):
    params = inspect.signature(fn).parameters
    for param in params.values():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True
    return False


def has_request_arg(fn):
    sig = inspect.signature(fn)
    params = sig.parameters
    found = False
    for name, param in params.items():
        if name == 'request':
            found = True
            continue
        if found and (param.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.VAR_KEYWORD)):
            raise ValueError('request parameter must be the last named parameter in function: %s%s' % (fn.__name__, str(sig)))
    return found


class RequestHandler(object):
    def __init__(self, app, fn):
        self._app = app
        self._func = fn
        self._has_request_arg = has_request_arg(fn)
        self._has_var_kw_arg = has_var_kw_arg(fn)
        self._has_named_kw_args = has_named_kw_args(fn)
        self._named_kw_args = get_named_kw_args(fn)
        self._required_kw_args = get_required_kw_args(fn)

    async def __call__(self, request):
        kw = None
        if self._has_named_kw_args or self._has_var_kw_arg:
            if request.method == 'POST':
                if not request.content_type:
                    return web.HTTPBadRequest('Missing Content-Type.')
                ct = request.content_type.lower()
                if ct.startswith('application/json'):
                    params = await request.json()
                    if not isinstance(params, dict):
                        return web.HTTPBadRequest('JSON body must be object.')
                    kw = params
                elif ct.startswith('application/x-www-form-urlencoded') or ct.startswith('multipart/form-data'):
                    params = request.post()
                    kw = dict(**params)
                else:
                    return web.HTTPBadRequest('Unsupported Content-Type: %s' % request.content_type)
            if request.method == 'GET':
                qs = request.query_string
                if qs:
                    kw = dict()
                    params = parse.parse_qs(qs, keep_blank_values=True)
                    for k, v in params.items():
                        kw[k] = v[0] if len(v) == 1 else v

        if kw is None:
            kw = dict(**request.match_info)
        else:
            if not self._has_var_kw_arg and self._named_kw_args:
                # remove all unnamed kw args
                copy = dict()
                for name in self._named_kw_args:
                    if name in kw:
                        copy[name] = kw[name]
                kw = copy
            # check named args
            for k, v in request.match_info.items():
                if k in kw:
                    logging.warn('Duplicate arg name in named arg and kw args: %s' % k)
                kw[k] = v
        if self._has_request_arg:
            kw['request'] = request
        # check required kw
        if self._required_kw_args:
            for name in _required_kw_args:
                if name not in kw:
                    return web.HTTPBadRequest('Missing argument: %s' % name)
        logging.info('call with args: %s' % kw)
        try:
            r = await self._func(**kw)
            return r
        except APIError as e:
            return dict(error=e.error, data=e.data, message=e.message)


def add_static(app):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    app.router.add_static('/static/', path)
    logging.info('add static %s => %s' % ('/static/', path))


def add_route(app, fn):
    method = getattr(fn, '__method__', None)
    path = getattr(fn, '__route__', None)
    if method is None or path is None:
        raise ValueError('@get or @post not defined in %s' % fn)
    if not asyncio.iscoroutinefunction(fn) or inspect.isgeneratorfunction(fn):
        fn = asyncio.coroutine(fn)
    logging.info('add route %s %s => %s%s' % (method, path, fn.__name__, inspect.signature(fn)))
    app.router.add_route(method, path, RequestHandler(app, fn))


def add_routes(app, module_name):
    n = module_name.rfind('.')
    # if n == -1:
    #     mod = __import__(module_name, globals(), locals())
    # else:
    #     package = module_name[:n]
    #     name = module_name[n+1:]
    #     mod = getattr(__import__(package, globals(), locals(), [name]), name)
    mod = importlib.import_module(module_name)
    for attr in dir(mod):
        if attr.startswith('_'):
            continue
        fn = getattr(mod, attr)
        if callable(fn):
            method = getattr(fn, '__method__', None)
            path = getattr(fn, '__route__', None)
            if method and path:
                add_route(app, fn)
