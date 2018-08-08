from coroweb import get, post


@get('/')
def index(request):
    return '<h1>Awesome</h1>'
