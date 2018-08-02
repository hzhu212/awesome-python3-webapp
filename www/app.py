import logging; logging.basicConfig(level=logging.INFO)
from aiohttp import web

routes = web.RouteTableDef()


@routes.get('/')
async def index(request):
    return web.Response(body=b'<h1>Awesome</h1>', content_type='text/html')


if __name__ == '__main__':
    app = web.Application()
    app.add_routes(routes)
    web.run_app(app, host='127.0.0.1', port='8000')
