import logging
import functools
import hashlib
import json
import re
import time

from aiohttp import web

from apis import APIError, APIValueError, APIPermissionError
from config import configs
from coroweb import get, post
from models import User, Blog, Comment, next_id


COOKIE_NAME = 'awesession'
COOKIE_MAX_AGE = 86400
_COOKIE_KEY = configs.session.secret


def user2cookie(user, max_age):
    """generate cookie string by user"""
    # build cookie string by 'id-expires-sha1(id-password-expires-secret)'
    expires = str(int(time.time() + max_age))
    s = '%s-%s-%s-%s' % (user.id, user.password, expires, _COOKIE_KEY)
    lst = [user.id, expires, hashlib.sha1(s.encode('utf8')).hexdigest()]
    return '-'.join(lst)


async def cookie2user(cookie_str):
    if not cookie_str:
        return None
    try:
        lst = cookie_str.split('-')
        if len(lst) != 3:
            return None
        uid, expires, sha1 = lst
        if int(expires) < time.time():
            return None
        user = await User.find(uid)
        if user is None:
            return None
        s = '%s-%s-%s-%s' % (user.id, user.password, expires, _COOKIE_KEY)
        if sha1 != hashlib.sha1(s.encode('utf8')).hexdigest():
            logging.info('Invalid sha1')
            return None
        user.password = '******'
        return user
    except Exception as e:
        logging.exception(e)
        return None


def require_signin(func):
    @functools.wraps(func)
    def wrapper(*args, **kw):
        try:
            request = args[-1]
            assert request.__user__ is not None
        except:
            return web.HTTPFound('/signin')
        return func(*args, **kw)
    return wrapper


def require_admin(func):
    @functools.wraps(func)
    def wrapper(*args, **kw):
        try:
            request = args[-1]
            assert request.__user__.admin
        except:
            raise APIPermissionError()
        return func(*args, **kw)
    return wrapper


@get('/')
async def index(request):
    summary = 'Lorem ipsum dolor sit amet, consectetur adipisicing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.'
    blogs = [
        Blog(id='1', name='Test Blog', summary=summary, created_at=time.time()-120),
        Blog(id='2', name='Something New', summary=summary, created_at=time.time()-3600),
        Blog(id='3', name='Learn Swift', summary=summary, created_at=time.time()-7200)
    ]
    return {
        '__template__': 'blogs.html',
        'blogs': blogs,
        'user': request.__user__,
    }


@get('/blog/{id_}')
async def get_blog(request, *, id_):
    blog = await Blog.find(id_)
    comments = await Comment.find_all('blog_id=?', (id_,), order_by='created_at desc')
    current_user = request.__user__
    return {
        '__template__': 'blog.html',
        'blog': blog,
        'comments': comments,
        'user': current_user,
    }


@get('/register')
def register():
    return {'__template__': 'register.html'}


@get('/signin')
def signin():
    return {'__template__': 'signin.html'}


@get('/signout')
def signout(request):
    referer = request.headers.get('Referer')
    r = web.HTTPFound(referer or '/')
    r.set_cookie(COOKIE_NAME, '-deleted-', max_age=0, httponly=True)
    logging.info('user signed out.')
    return r


@require_admin
@get('/manage/blogs/create')
def manage_create_blog():
    return {
        '__template__': 'manage_blog_edit.html',
        'id': '',
        'action': '/api/blogs',
    }


@post('/api/authenticate')
async def authenticate(*, email, password):
    if not email:
        raise APIValueError('email', 'Invalid email.')
    if not password:
        raise APIValueError('password', 'Invalid password.')
    users = await User.find_all(where='email=?', args=(email,))
    if len(users) == 0:
        raise APIValueError('email', 'Email not exists.')
    user = users[0]
    # check password. password stored in database is: sha1(id:password)
    s = '%s:%s' % (user.id, password)
    if user.password != hashlib.sha1(s.encode('utf8')).hexdigest():
        raise APIValueError('password', 'Invalid password.')
    # authenticate OK, set cookie
    r = web.Response()
    r.set_cookie(COOKIE_NAME, user2cookie(user, COOKIE_MAX_AGE), max_age=COOKIE_MAX_AGE, httponly=True)
    r.content_type = 'application/json'
    user.password = '******'
    r.body = json.dumps(user, ensure_ascii=False).encode('utf8')
    return r


_RE_EMAIL = re.compile(r'^[a-z0-9\.\-\_]+\@[a-z0-9\-\_]+(\.[a-z0-9\-\_]+){1,4}$')
_RE_SHA1 = re.compile(r'^[0-9a-f]{40}$')


@post('/api/users')
async def api_register_user(*, email, name, password):
    if not name or not name.strip():
        raise APIValueError('name')
    if not email or not _RE_EMAIL.match(email):
        raise APIValueError('email')
    if not password or not _RE_SHA1.match(password):
        raise APIValueError('password')
    users = await User.find_all('email=?', (email,))
    if len(users) > 0:
        raise APIError('register:failed', 'email', 'Email is already in use.')
    uid = next_id()
    sha1_password = hashlib.sha1('{}:{}'.format(uid, password).encode('utf8')).hexdigest()
    avatar = 'http://www.gravatar.com/avatar/%s?d=retro&s=120' % hashlib.md5(email.encode('utf-8')).hexdigest()
    user = User(id=uid, name=name.strip(), email=email, password=sha1_password, image=avatar)
    await user.save()
    # make session cookie
    r = web.Response()
    r.set_cookie(COOKIE_NAME, user2cookie(user, COOKIE_MAX_AGE), max_age=COOKIE_MAX_AGE, httponly=True)
    r.content_type = 'application/json'
    user.password = '******'
    r.body = json.dumps(user, ensure_ascii=False).encode('utf8')
    return r


@get('/api/blogs/{id_}')
async def api_get_blog(*, id_):
    blog = await Blog.find(id_)
    return blog


@require_admin
@post('/api/blogs')
async def api_create_blog(request, *, name, summary, content):
    if not name or not name.strip():
        raise APIValueError('name', 'name cannot be empty.')
    if not summary or not summary.strip():
        raise APIValueError('summary', 'summary cannot be empty.')
    if not content or not content.strip():
        raise APIValueError('content', 'content cannot be empty.')
    user = request.__user__
    blog = Blog(user_id=user.id, user_name=user.name, user_image=user.image, name=name.strip(), summary=summary.strip(), content=content.strip())
    await blog.save()
    return blog
