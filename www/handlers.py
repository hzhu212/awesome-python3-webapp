import logging
import functools
import hashlib
import json
import re
import time

from aiohttp import web

from apis import APIError, APIValueError, APIPermissionError, APIResourceNotFoundError, Page
from config import configs
from coroweb import get, post, require_admin, require_signin
from models import User, Blog, Comment, next_id


COOKIE_NAME = 'awesession'
COOKIE_MAX_AGE = 86400
_COOKIE_KEY = configs.session.secret


def get_page_index(page_str):
    p = 1
    try:
        p = int(page_str)
    except ValueError as e:
        pass
    if p < 1:
        p = 1
    return p


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


@get('/')
async def index(*, page='1'):
    page_index = get_page_index(page)
    nblogs = await Blog.find_number('count(id)')
    page = Page(nblogs, page_index)
    if nblogs == 0:
        blogs = []
    else:
        blogs = await Blog.find_all(order_by='created_at desc', limit=(page.offset, page.limit))
    return {
        '__template__': 'blogs.html',
        'page': page,
        'blogs': blogs,
    }


@get('/blog/{id_}')
async def get_blog(request, *, id_):
    blog = await Blog.find(id_)
    comments = await Comment.find_all('blog_id=?', (id_,), order_by='created_at desc')
    return {
        '__template__': 'blog.html',
        'blog': blog,
        'comments': comments,
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


@get('/permission_denied')
def permission_denied():
    return {'__template__': 'permission_denied.html'}


@require_admin
@get('/manage/users')
def manage_users(*, page='1'):
    return {
        '__template__': 'manage_users.html',
        'page_index': get_page_index(page),
    }


@require_admin
@get('/manage/blogs')
def manage_blogs(*, page='1'):
    return {
        '__template__': 'manage_blogs.html',
        'page_index': get_page_index(page),
    }


@require_admin
@get('/manage/blogs/create')
def manage_create_blog():
    return {
        '__template__': 'manage_blog_edit.html',
        'id': '',
        'action': '/api/blogs',
    }


@require_admin
@get('/manage/blogs/edit')
def manage_edit_blog(*, id):
    return {
        '__template__': 'manage_blog_edit.html',
        'id': id,
        'action': '/api/blogs/' + id,
    }


@require_admin
@get('/manage/comments')
def manage_comments(*, page='1'):
    return {
        '__template__': 'manage_comments.html',
        'page_index': get_page_index(page),
    }


@post('/api/authenticate')
async def api_authenticate(*, email, password):
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


@get('/api/blogs')
async def api_get_blogs(*, page='1'):
    page_index = get_page_index(page)
    nblogs = await Blog.find_number('count(id)')
    p = Page(nblogs, page_index)
    if nblogs == 0:
        return dict(page=p, blogs=())
    blogs = await Blog.find_all(order_by='created_at desc', limit=(p.offset, p.limit))
    return dict(page=p, blogs=blogs)


@get('/api/blogs/{id_}')
async def api_get_blog(*, id_):
    blog = await Blog.find(id_)
    if blog is None:
        raise APIResourceNotFoundError('Blog')
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


@require_admin
@post('/api/blogs/{blog_id}')
async def api_update_blog(blog_id, *, name, summary, content):
    if not name or not name.strip():
        raise APIValueError('name', 'name cannot be empty.')
    if not summary or not summary.strip():
        raise APIValueError('summary', 'summary cannot be empty.')
    if not content or not content.strip():
        raise APIValueError('content', 'content cannot be empty.')
    blog = await Blog.find(blog_id)
    if blog is None:
        raise APIResourceNotFoundError('Blog')
    blog.name = name.strip()
    blog.summary = summary.strip()
    blog.content = content.strip()
    await blog.update()
    return blog


@require_admin
@post('/api/blogs/{blog_id}/delete')
async def api_delete_blog(blog_id):
    blog = await Blog.find(blog_id)
    if blog is None:
        raise APIResourceNotFoundError('Blog')
    await blog.remove()
    return dict(id=blog_id)


@get('/api/comments')
async def api_get_comments(*, page='1'):
    page_index = get_page_index(page)
    ncomments = await Comment.find_number('count(id)')
    p = Page(ncomments, page_index)
    if ncomments == 0:
        return dict(page=p, comments=())
    comments = await Comment.find_all(order_by='created_at desc', limit=(p.offset, p.limit))
    return dict(page=p, comments=comments)


@require_signin
@post('/api/blogs/{blog_id}/comments')
async def api_create_comment(blog_id, request, *, content):
    if not content or not content.strip():
        raise APIValueError('content', 'content cannot be empty.')
    user = request.__user__
    comment = Comment(blog_id=blog_id, user_id=user.id, user_name=user.name, user_image=user.image, content=content.strip())
    await comment.save()
    return comment


@require_admin
@post('/api/comments/{comment_id}/delete')
async def api_delete_comment(comment_id):
    comment = await Comment.find(comment_id)
    if comment is None:
        raise APIResourceNotFoundError('Comment')
    await comment.remove()
    return dict(id=comment_id)


@require_admin
@get('/api/users')
async def api_get_users(*, page='1'):
    page_index = get_page_index(page)
    nusers = await User.find_number('count(id)')
    if nusers == 0:
        return dict(page=p, users=())
    p = Page(nusers, page_index)
    users = await User.find_all(order_by='created_at desc', limit=(p.offset, p.limit))
    return dict(page=p, users=users)
