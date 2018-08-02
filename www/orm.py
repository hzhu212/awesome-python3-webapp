import logging
import aiomysql


def log(sql, args=()):
    logging.info('SQL: %s' % sql)


async def create_pool(loop, **kw):
    logging.info('creating database connection pool...')
    global __pool
    __pool = await aiomysql.create_pool(
        host=kw.get('host', 'localhost'),
        port=kw.get('port', 3306),
        user=kw['user'],
        password=kw['password'],
        db=kw['db'],
        charset=kw.get('charset', 'utf8mb4'),
        autocommit=kw.get('autocommit', True),
        maxsize=kw.get('maxsize', 10),
        minsize=kw.get('minsize', 1),
        loop=loop
        )


async def select(sql, args, size=None):
    log(sql, args)
    global __pool
    with (await __pool) as conn:
        cur = await conn.cursor(aiomysql.DictCursor)
        await cur.execute(sql.replace('?', '%s'), args or ())
        if size:
            res = await cur.fetchmany(size)
        else:
            res = await cur.fetchall()
        await cur.close()

        logging.info('rows returned: %s' % len(res))
        return res


async def execute(sql, args):
    log(sql, args)
    global __pool
    with (await __pool) as conn:
        try:
            cur = await conn.cursor()
            await cur.execute(sql.replace('?', '%s'), args)
            affected = cur.rowcount
            await cur.close()
        except:
            raise
        return affected


def create_args_string(num):
    return ', '.join(['?'] * num)


class Field(object):
    """Base field class"""
    def __init__(self, name, column_type, primary_key, default):
        self.name = name
        self.column_type = column_type
        self.primary_key = primary_key
        self.default = default

    def __str__(self):
        return '<%s, %s:%s>' % (self.__class__.__name__, self.column_type, self.name)


class StringField(Field):
    def __init__(self, name=None, primary_key=False, default=None, ddl='varchar(100)'):
        super().__init__(name, ddl, primary_key, default)


class BooleanField(Field):
    def __init__(self, name=None, default=False):
        super().__init__(name, 'boolean', False, default)


class IntegerField(Field):
    def __init__(self, name=None, primary_key=False, default=0):
        super().__init__(name, 'bigint', primary_key, default)


class FloatField(Field):
    def __init__(self, name=None, primary_key=False, default=0.0):
        super().__init__(name, 'real', primary_key, default)


class TextField(Field):
    def __init__(self, name=None, default=None):
        super().__init__(name, 'text', False, default)


class ModelMetaclass(type):
    def __new__(cls, name, bases, attrs):
        # 排除 Model 类本身
        if name == 'Model':
            return type.__new__(cls, name, bases, attrs)
        table_name = attrs.get('__table__', name)
        logging.info('found model %s: (table %s)' % (name, table_name))
        mappings = dict()
        fields = []
        primary_key = None
        for k, v in attrs.items():
            if isinstance(v, Field):
                logging.info('  found mapping: %s ==> %s' % (k, v))
                mappings[k] = v
                if v.primary_key:
                    if primary_key is not None:
                        raise StandardError('Duplicate primary key for field: %s' % k)
                    primary_key = k
                else:
                    fields.append(k)
        if not primary_key:
            raise StandardError('Primary key not found')
        for k in mappings.keys():
            attrs.pop(k)
        escaped_fields = list(map(lambda f: '`%s`' % f, fields))
        attrs['__mappings__'] = mappings
        attrs['__table__'] = table_name
        attrs['__primary_key__'] = primary_key
        attrs['__fields__'] = fields # 除主键外的属性名
        # 构造默认的 SELECT，INSERT，UPDATE 和 DELETE 语句
        attrs['__select__'] = 'select `%s`, %s from `%s`' % (primary_key, ', '.join(escaped_fields), table_name)
        attrs['__insert__'] = 'insert into `%s` (%s, `%s`) values (%s)' % (table_name, ', '.join(escaped_fields), primary_key, create_args_string(len(fields)+1))
        attrs['__update__'] = 'update `%s` set %s where `%s`=?' % (table_name, ', '.join(map(lambda f: '`%s=?`' % (mappings.get(f).name or f), fields)), primary_key)
        attrs['__delete__'] = 'delete from `%s` where `%s`=?' % (table_name, primary_key)
        return type.__new__(cls, name, bases, attrs)


class Model(dict, metaclass=ModelMetaclass):
    """Base model class"""
    def __init__(self, **kw):
        super().__init__(**kw)

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError('"%s" object has no attribute "%s"' % (self.__class__.__name__, key))

    def __setattr__(self, key, value):
        self[key] = value

    def get_value(self, key):
        return self.get(key, None)

    def get_value_or_default(self, key):
        value = self.get(key, None)
        if value is None:
            field = self.__mappings__[key]
            if field.default is not None:
                value = field.default() if callable(field.default) else field.default
                logging.debug('using default value for %s: %s' %(key, value))
                setattr(self, key, value)
        return value

    @classmethod
    async def find_all(cls, where=None, args=None, **kw):
        """find object by where clause."""
        sql = [cls.__select__]
        if where:
            sql.extend(['where', where])
        if args is None:
            args = []
        order_by = kw.get('order_by', None)
        if order_by:
            sql.extend(['order by', order_by])
        limit = kw.get('limit')
        if limit is not None:
            if isinstance(limit, int):
                sql.append('limit ?')
                args.append(limit)
            elif isinstance(limit, (tuple, list)) and len(limit) == 2:
                sql.append('limit ?, ?')
                args.extend(limit)
            else:
                raise ValueError('Invalid limit value: %s' % limit)
        res = await select(' '.join(sql), args)
        return (cls(**obj) for obj in res)

    @classmethod
    async def find_number(cls, select_field, where=None, args=None):
        """find number by select and where"""
        sql = ['select %s as _num_ from `%s`' % (select_field, cls.__table__)]
        if where:
            sql.extend(['where', where])
        res = await execute(' '.join(sql), args, 1)
        if len(res) == 0:
            return None
        return res[0]['_num_']

    @classmethod
    async def find(cls, pk):
        """find object by primary key."""
        sql = '%s where `%s`=?' % (cls.__select__, cls.__primary_key__)
        res = await select(sql, (pk,), 1)
        if len(res) == 0:
            return None
        return cls(**res[0])

    async def save(self):
        """save object to database"""
        args = list(map(self.get_value_or_default, self.__fields__))
        args.append(self.get_value_or_default(self.__primary_key__))
        nrow = await execute(self.__insert__, args)
        if nrow != 1:
            logging.warn('failed to insert record: affected rows: %s' % nrow)

    async def update(self):
        """update record by primary key"""
        args = list(map(self.get_value, self.__fields__))
        args.append(self.get_value(self.__primary_key__))
        nrow = await execute(self.__update__, args)
        if nrow != 1:
            logging.warn('failed to update record: affected rows: %s' % nrow)

    async def remove(self):
        """delete record by primary key"""
        args = [self.get_value(self.__primary_key__)]
        nrow = await execute(self.__delete__, args)
        if nrow != 1:
            logging.warn('failed to delete record: affected rows: %s' % nrow)
