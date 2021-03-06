'''
sgqlc - Simple GraphQL Client
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

GraphQL Types in Python
=======================

This module fulfill two purposes:

 - declare GraphQL schema in Python, just declare classes inheriting
   :class:`Type`, :class:`Interface` and fill them with
   :class:`Field` (or base types: ``str``, ``int``, ``float``,
   ``bool``). You may as well declare :class:`Enum` with
   ``__choices__`` or :class:`Union` and ``__types__``. Then
   ``__str__()`` will provide nice printout and ``__repr__()`` will
   return the GraphQL declarations (which can be tweaked with
   ``__to_graphql__()``, giving indent details). ``__bytes__()`` is
   also provided, mapping to a compact ``__to_graphql__()`` version,
   without indent.

 - Interpret GraphQL JSON data, by instantiating the declared classes
   with such information. While for scalar types it's just a
   pass-thru, for :class:`Type` and :class:`Interface` these will use
   the fields to provide native object with attribute or key access
   mapping to JSON, instead of ``json_data['key']['other']`` you may
   use ``obj.key.other``. Newly declared types, such as ``DateTime``
   will take care to generate native Python objects (ie:
   ``datetime.datetime``). Setting such attributes will also update
   the backing store object, including converting back to valid JSON
   values.

These two improve usability of GraphQL **a lot**, pretty much like
Django's Model helps to access data bases.

:class:`Field` may be created explicitly, with information such as
target type, arguments and GraphQL name. However, more commonly these
are auto-generated by the container: GraphQL name, usually
``aFieldName`` will be created from Python name, usually
``a_field_name``. Basic types such as ``int``, ``str``, ``float`` or
``bool`` will map to ``Int``, ``String``, ``Float`` and ``Boolean``.

The end-user classes and functions provided by this module are:

 - :class:`Schema`: top level object that will contain all
   declarations. For single-schema applications, you don't have to
   care about this since types declared without an explicit
   ``__schema__ = SchemaInstance`` member will end in the
   ``global_schema``.

 - :class:`Scalar`: "pass thru" everything received. Base for other
   scalar types:

    * :class:`Int`: ``int``
    * :class:`Float`: ``float``
    * :class:`String`: ``str``
    * :class:`Boolean`: ``bool``
    * :class:`ID`: ``str``

 - :class:`Enum`: also handled as a ``str``, but GraphQL syntax needs
   them without the quotes, so special handling is done. Validation is
   done using ``__choices__`` member, which is either a string (which
   will be splitted using ``str.split()``) or a list/tuple of
   strings with values.

 - :class:`Union`: defines the target type of a field may be one of
   the given ``__types__``.

 - Container types: :class:`Type`, :class:`Interface` and
   :class:`Input`. These are similar in usage, but GraphQL needs them
   defined differently. They are composed of :class:`Field`. A field
   may have arguments (:class:`ArgDict`), which is a set of
   :class:`Arg`. Arguments may contain default values or
   :class:`Variable`, which will be sent alongside the query (this
   allows to generate the query once and use variables, letting the
   server to use both together).

 - :func:`non_null()`, maps to GraphQL ``Type!`` and enforces the
   object is not ``None``.

 - :func:`list_of()`, maps to GraphQL ``[Type]`` and enforces the
   object is a list of ``Type``.

This module only provide built-in scalar types. However, two other
modules will extend the behavior for common conventions:

 - :mod:`sgqlc.types.datetime` will declare ``DateTime``, ``Date`` and
   ``Time``, mapping to Python's :mod:`datetime`. This also allows
   fields to be declared as ``my_date = datetime.date``,

 - :mod:`sgqlc.types.relay` will declare ``Node`` and ``Connection``,
   matching `Relay <https://facebook.github.io/relay/>`_ `Global
   Object Identification
   <https://facebook.github.io/relay/graphql/objectidentification.htm>`_
   and `Cursor Connections
   <https://facebook.github.io/relay/graphql/connections.htm>`_, which
   are widely used.

:license: ISC
'''

__docformat__ = 'reStructuredText en'

import json
from collections import OrderedDict

__all__ = (
    'Schema', 'Scalar', 'Enum', 'Union', 'Variable', 'Arg', 'ArgDict',
    'Field', 'Type', 'Interface', 'Input', 'Int', 'Float', 'String',
    'Boolean', 'ID', 'non_null', 'list_of',
)


class ODict(OrderedDict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError('%s has no field %s' % (self, name)) from exc


class Schema:
    '''The schema will contain declared types.

    There is a default schema called ``global_schema``, a singleton
    that is automatically assigned to every type that does not provide
    its own schema.

    Once types are constructed, they are automatically added to the
    schema as properties of the same name, for example
    :class:`Int` is exposed as ``schema.Int``,
    ``schema['Int']`` or ``schema.scalars['Int']``.

    New schema will inherit the types defined at ``base_schema``,
    which defaults to ``global_schema``, at the time of their
    creation. However types added to ``base_schema`` after the schema
    creation are not automatically picked by existing schema. The copy
    happens at construction time.

    New types may be added to schema using ``schema += type`` and
    removed with ``schema -= type``. However those will not affect
    their member ``type.__schema__``, which remains the same (where they
    where originally created).

    The schema is an iterator that will report all registered types.
    '''
    __slots__ = ('__all', '__kinds', '__cache__')

    def __init__(self, base_schema=None):
        self.__all = OrderedDict()
        self.__kinds = {}
        self.__cache__ = {}

        if base_schema is None:
            try:
                # on the first execution, global_schema is created, thus it's
                # not available.
                base_schema = global_schema
            except NameError:
                pass

        if base_schema is not None:
            self.__all.update(base_schema.__all)
            for k, v in base_schema.__kinds.items():
                self.__kinds.setdefault(k, ODict()).update(v)

    def __contains__(self, key):
        return key in self.__all

    def __getitem__(self, key):
        return self.__all[key]

    def __getattr__(self, key):
        try:
            return self.__kinds[key]  # .type, .scalar, etc...
        except KeyError:
            pass
        try:
            return self.__all[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __iter__(self):
        return iter(self.__all.values())

    def __iadd__(self, typ):
        '''Manually add a type to the schema.

        Types are automatically once their class is created. Only use
        this if you're copying a type from one schema to another.

        Note that the type name ``str(typ)`` must not exist in the
        schema, otherwise :class:`ValueError` is raised.

        To remove a type, use ``schema -= typ``.
        '''
        name = typ.__name__
        t = self.__all.setdefault(name, typ)
        if t is not typ:
            raise ValueError('%s already has %s=%s' %
                             (self.__class__.__name__, name, typ))
        self.__kinds.setdefault(typ.__kind__, ODict()).update({name: typ})
        return self

    def __isub__(self, typ):
        '''Remove a type from the schema.

        This may be of use to override some type, such as
        :class:`sgqlc.types.datetime.Date` or
        :class:`sgqlc.types.datetime.DateTime`.
        '''
        name = typ.__name__
        del self.__all[name]
        del self.__kinds[typ.__kind__][name]
        return self

    def __str__(self):
        return '{' + ', '.join(str(e) for e in self) + '}'

    def __to_graphql__(self, indent=0, indent_string='  '):
        prefix = indent_string * indent
        s = [prefix + 'schema {']
        s.extend(e.__to_graphql__(indent + 1, indent_string) for e in self)
        s.append(prefix + '}')
        return '\n'.join(s)

    def __repr__(self):
        return self.__to_graphql__()

    def __bytes__(self):
        return bytes(self.__to_graphql__(indent_string=''), 'utf-8')


global_schema = Schema()


class BaseMeta(type):
    'Automatically adds class to its schema'
    def __init__(cls, name, bases, namespace):
        super(BaseMeta, cls).__init__(name, bases, namespace)
        if not bases or BaseType in bases or ContainerType in bases:
            return

        auto_register_name = '_%s__auto_register' % (name,)
        auto_register = getattr(cls, auto_register_name, True)

        if auto_register:
            cls.__schema__ += cls

    def __str__(cls):
        return cls.__name__

    def __to_graphql__(cls, indent=0, indent_string='  '):
        prefix = indent_string * indent
        return '%s%s %s' % (prefix, cls.__kind__, cls.__name__)

    def __repr__(cls):
        return cls.__to_graphql__()

    def __bytes__(cls):
        return bytes(cls.__to_graphql__(indent_string=''), 'utf-8')

    def __ensure__(cls, t):
        if isinstance(t, type) and issubclass(t, cls):
            return t
        try:
            return map_python_to_graphql[t]
        except KeyError as exc:
            raise TypeError('Not %s or mapped: %s' % (cls, t)) from exc


class BaseType(metaclass=BaseMeta):
    '''Base shared by all GraphQL classes.

    '''
    __schema__ = global_schema
    __kind__ = None


def non_null(t):
    '''Generates non-null type (t!)
    '''
    t = BaseType.__ensure__(t)
    name = t.__name__ + '!'
    try:
        return t.__schema__.__cache__[name]
    except KeyError:
        pass

    def __new__(cls, json_data, selection_list=None):
        if json_data is None:
            raise ValueError(name + ' received null value')
        return t(json_data)

    def __to_graphql_input__(value, indent=0, indent_string='  '):
        return t.__to_graphql_input__(value, indent, indent_string)

    wrapper = type(name, (t,), {
        '__new__': __new__,
        '_%s__auto_register' % name: False,
        '__to_graphql_input__': __to_graphql_input__,
    })
    t.__schema__.__cache__[name] = wrapper
    return wrapper


def list_of(t):
    '''Generates list of types ([t])
    '''
    t = BaseType.__ensure__(t)
    name = '[' + t.__name__ + ']'
    try:
        return t.__schema__.__cache__[name]
    except KeyError:
        pass

    def __new__(cls, json_data, selection_list=None):
        if json_data is None:
            return None
        return [t(v, selection_list) for v in json_data]

    def __to_graphql_input__(value, indent=0, indent_string='  '):
        if value is None:
            return None
        r = []
        for v in value:
            r.append(t.__to_graphql_input__(v, indent, indent_string))
        return '[' + ', '.join(r) + ']'

    def __to_json_value__(value):
        if value is None:
            return None
        return [t.__to_json_value__(v) for v in value]

    wrapper = type(name, (t,), {
        '__new__': __new__,
        '_%s__auto_register' % name: False,
        '__to_graphql_input__': __to_graphql_input__,
        '__to_json_value__': __to_json_value__,
    })
    t.__schema__.__cache__[name] = wrapper
    return wrapper


class Scalar(BaseType):
    '''Basic scalar types, passed thru (no conversion).

    This may be used directly if no special checks or conversions are
    needed. Otherwise use subclasses, like :class:`Int`,
    :class:`Float`, :class:`String`, :class:`Boolean`, :class:`ID`...

    Scalar classes will never produce instance of themselves, rather
    return the converted value (int, bool...)
    '''
    __kind__ = 'scalar'

    def converter(value):
        return value

    def __new__(cls, json_data, selection_list=None):
        return None if json_data is None else cls.converter(json_data)

    @classmethod
    def __to_graphql_input__(cls, value, indent=0, indent_string='  '):
        return json.dumps(cls.__to_json_value__(value))

    @classmethod
    def __to_json_value__(cls, value):
        return value


class EnumMeta(BaseMeta):
    'meta class to set enumeration attributes, __contains__, __iter__...'
    def __init__(cls, name, bases, namespace):
        super(EnumMeta, cls).__init__(name, bases, namespace)
        if not cls.__choices__ and BaseType not in bases:
            raise ValueError(name + ': missing __choices__')

        if isinstance(cls.__choices__, str):
            cls.__choices__ = tuple(cls.__choices__.split())
        else:
            cls.__choices__ = tuple(cls.__choices__)

        for v in cls.__choices__:
            setattr(cls, v, v)

    def __contains__(cls, v):
        return v in cls.__choices__

    def __iter__(cls):
        return iter(cls.__choices__)

    def __len__(cls):
        return len(cls.__choices__)

    def __to_graphql__(cls, indent=0, indent_string='  '):
        s = [BaseMeta.__to_graphql__(cls, indent, indent_string)]
        prefix = indent_string * (indent + 1)
        for c in cls:
            s.append(prefix + str(c))
        s.append(indent_string * indent + '}')
        return '\n'.join(s)

    def __to_graphql_input__(cls, value, indent=0, indent_string='  '):
        return value

    def __to_json_value__(cls, value):
        return value


class Enum(BaseType, metaclass=EnumMeta):
    '''This is an abstract class that enumerations should inherit
    and define ``__choices__`` class member with a list of strings
    matching the choices allowed by this enumeration. A single string
    may also be used, in such case it will be split using
    ``str.split()``.

    Note that ``__choices__`` is not set in the final class, the
    metaclass will use that to build members and provide the
    ``__iter__``, ``__contains__`` and ``__len__`` instead.
    '''
    __kind__ = 'enum'
    __choices__ = ()

    def __new__(cls, json_data, selection_list=None):
        if json_data is None:
            return None
        if json_data not in cls:
            raise ValueError('%s does not accept value %s' % (cls, json_data))
        return json_data


class Union(BaseType):
    '''This is an abstract class that union of multiple types should
    inherit and define ``__types__``, a list of pre-defined
    :class:`Type`.
    '''

    __kind__ = 'union'
    __types__ = ()

    @classmethod
    def __iter__(cls):
        return iter(cls.__types__)

    @classmethod
    def __contains__(cls, name_or_type):
        if isinstance(name_or_type, str):
            name_or_type = cls.__schema__[name_or_type]
        return name_or_type in cls.__types__

    @classmethod
    def __to_graphql__(cls, indent=0, indent_string='  '):
        suffix = ' = ' + ' | '.join(str(c) for c in cls.__types__)
        return BaseMeta.__to_graphql__(cls, indent, indent_string) + suffix


class ContainerTypeMeta(BaseMeta):
    '''Creates container types, ensures fields are instance of Field.
    '''
    def __init__(cls, name, bases, namespace):
        super(ContainerTypeMeta, cls).__init__(name, bases, namespace)
        cls.__fields = OrderedDict()
        cls.__interfaces__ = ()

        if not bases or BaseType in bases or ContainerType in bases:
            return

        if cls.__kind__ == 'interface':
            cls.__fix_type_kind(bases)

        cls.__populate_interfaces(bases)
        cls.__inherit_fields(bases)
        cls.__create_own_fields()

    def __fix_type_kind(cls, bases):
        for b in bases:
            if b.__kind__ == 'type':
                cls.__kind__ = 'type'
                break

    def __populate_interfaces(cls, bases):
        ifaces = []
        for b in bases:
            if getattr(b, '__kind__', '') == 'interface':
                ifaces.append(b)
            for i in getattr(b, '__interfaces__', []):
                if i not in ifaces:
                    ifaces.append(i)

        cls.__interfaces__ = tuple(ifaces)

    def __inherit_fields(cls, bases):
        for b in bases:
            cls.__fields.update(b.__fields)

    def __create_own_fields(cls):
        for name in dir(cls):
            if name.startswith('_'):
                continue

            field = getattr(cls, name)
            if not isinstance(field, Field):
                try:
                    field = BaseType.__ensure__(field)
                except TypeError as e:
                    continue

                field = Field(field)

            field._set_container(cls.__schema__, cls, name)
            cls.__fields[name] = field
            delattr(cls, name)  # let fallback to cls.__fields using getitem

    def __getitem__(cls, key):
        try:
            return cls.__fields[key]
        except KeyError as exc:
            raise KeyError('%s has no field %s' % (cls, key)) from exc

    def __getattr__(cls, key):
        try:
            return cls.__fields[key]
        except KeyError as exc:
            raise AttributeError('%s has no field %s' % (cls, key)) from exc

    def __dir__(cls):
        original_dir = super(ContainerTypeMeta, cls).__dir__(cls)
        try:
            fields = list(cls.__fields.keys())
        except AttributeError as e:
            fields = []
        return sorted(original_dir + fields)

    def __iter__(cls):
        return iter(cls.__fields.values())

    def __contains__(cls, field_name):
        return field_name in cls.__fields

    def __to_graphql__(cls, indent=0, indent_string='  '):
        d = BaseMeta.__to_graphql__(cls, indent, indent_string)
        if hasattr(cls, '__interfaces__') and cls.__interfaces__:
            d += ' implements ' + ', '.join(str(i) for i in cls.__interfaces__)

        s = [d + ' {']
        prefix = indent_string * (indent + 1)
        for f in cls:
            s.append(prefix + f.__to_graphql__(indent, indent_string))
        s.append(indent_string * indent + '}')
        return '\n'.join(s)

    def __to_json_value__(cls, value):
        if value is None:
            return None
        d = {}
        for name, f in cls.__fields.items():
            # elements may not exist since not queried and would
            # trigger exception for non-null fields
            if name in value:
                d[f.graphql_name] = f.type.__to_json_value__(value[name])
        return d


class ContainerType(BaseType, metaclass=ContainerTypeMeta):
    '''Container of :class:`Field`.

    For ease of use, fields can be declared by sub classes in the
    following ways:

     - ``name = str`` to create a simple string field. Other basic
       types are allowed as well: ``int``, ``float``, ``str``,
       ``bool``, ``datetime.time``, ``datetime.date`` and
       ``datetime.datetime``. These are only used as identifiers to
       translate using ``map_python_to_graphql`` dict. Note that
       ``id``, although is not a type, maps to ``ID``.

     - ``name = TypeName`` for subclasses of ``BaseType``, such
       as pre-defined scalars (:class:`Int`, etc) or your own defined
       types, from :class:`Type`.

     - ``name = Field(TypeName, graphql_name='differentName',
       args={...})`` to explicitly define more field information,
       such as GraphQL JSON name, query parameters, etc.

    The metaclass :class:`ContainerTypeMeta` will normalize all of those
    members to be instances of :class:`Field`, as well as provide
    useful container protocol such as ``__contains__``,
    ``__getitem__``, ``__iter__`` and so on.

    Fields from all bases (interfaces, etc) are merged.

    Members started with underscore (``_``) are not processed.
    '''

    def __init__(self, json_data, selection_list=None):
        object.__setattr__(self, '__selection_list__', selection_list)
        cache = OrderedDict()
        object.__setattr__(self, '__fields_cache__', cache)
        if json_data is None:
            # backing store, changed by setattr()
            object.__setattr__(self, '__json_data__', {})
            return

        def set_field(name, field):
            graphql_name = field.graphql_name
            if graphql_name in json_data:
                try:
                    value = json_data[graphql_name]
                    value = field.type(value)
                    setattr(self, name, value)
                    cache[name] = field
                except Exception as exc:
                    raise ValueError('%s selection %r: %r (%s)' % (
                        self.__class__, name, value, exc)) from exc

        if self.__selection_list__ is not None:
            for sel in self.__selection_list__:
                field = sel.__field__
                name = sel.__alias__ or field.name
                set_field(name, field)
        else:
            for field in self.__class__:
                set_field(field.name, field)

        # backing store, changed by setattr()
        object.__setattr__(self, '__json_data__', json_data)

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        if not hasattr(self, '__json_data__'):  # still populating
            return
        # apply changes to json backing store, if name is known
        field = self.__fields_cache__.get(name)
        if field is None:
            return
        json_value = field.type.__to_json_value__(value)
        self.__json_data__[field.graphql_name] = json_value

    def __getitem__(self, name):
        try:
            return getattr(self, name)
        except AttributeError as exc:
            raise KeyError('%s has no field %s' % (self, name)) from exc

    def __setitem__(self, name, value):
        setattr(self, name, value)

    def __iter__(self):
        return iter(self.__fields_cache__.keys())

    def __contains__(self, name):
        return hasattr(self, name)

    def __len__(self):
        i = 0
        for name in self:
            i += 1
        return i

    def __str__(self):
        r = []
        for k in self:
            r.append('%s=%s' % (k, self[k]))
        return '%s(%s)' % (self.__class__.__name__, ', '.join(r))

    def __repr__(self):
        r = []
        for k in self:
            r.append('%s=%r' % (k, self[k]))
        return '%s(%s)' % (self.__class__.__name__, ', '.join(r))

    def __to_json_value__(self):
        return ContainerTypeMeta.__to_json_value__(self.__class__, self)

    def __bytes__(self):
        return bytes(json.dumps(
            self.__to_json_value__(),
            sort_keys=True, separators=(',', ':')), 'utf-8')


class BaseItem:
    '''Base item for :class:`Arg` and :class:`Field`.

    Each parameter has a GraphQL type, such as a derived class from
    :class:`Scalar` or :class:`Type`, this is used for nesting,
    conversion to native Python types, generating queries, etc.
    '''

    __slots__ = (
        '_type', 'graphql_name', 'name', 'schema', 'container',
    )

    def __init__(self, typ, graphql_name=None):
        '''
        :param typ: the :class:`Scalar` or :class:`Type` derived
          class. If this would cause a cross reference and the other
          type is not declared yet, then use the string name to query
          in the schema.
        :type typ: :class:`Scalar`, :class:`Type` or str

        :param graphql_name: the name to use in JSON object, usually ``aName``.
          If ``None`` or empty, will be created from python, converting
          ``a_name`` to ``aName`` using
          ``Arg._to_graphql_name()``
        :type graphql_name: str
        '''
        self._type = BaseType.__ensure__(typ)
        self.graphql_name = graphql_name
        self.name = None
        self.schema = None
        self.container = None

    def _set_container(self, schema, container, name):
        self.schema = schema
        self.container = container
        self.name = name
        if not self.graphql_name:
            self.graphql_name = self._to_graphql_name(name)

    @property
    def type(self):
        if not isinstance(self._type, str):
            return self._type
        return self.schema[self._type]

    @staticmethod
    def _to_graphql_name(name):
        '''Converts a Python name, ``a_name`` to GraphQL: ``aName``.
        '''
        parts = name.split('_')
        return ''.join(parts[:1] + [p.title() for p in parts[1:]])

    def __str__(self):
        return self.name

    def __to_graphql__(self, indent=0, indent_string='  '):
        return '%s: %s' % (self.graphql_name, self.type)

    def __repr__(self):
        return self.__to_graphql__()

    def __bytes__(self):
        return bytes(self.__to_graphql__(indent_string=''), 'utf-8')


class Variable:
    '''GraphQL variable: ``$varName``
    '''

    __slots__ = ('name',)

    def __init__(self, name):
        self.name = name

    def __str__(self):
        return self.__to_graphql__()

    def __repr__(self):
        return self.__to_graphql__()

    def __bytes__(self):
        return bytes(self.__to_graphql__(indent_string=''), 'utf-8')

    def __to_graphql__(self):
        return '$' + self.name

    @classmethod
    def __to_graphql_input__(cls, value, indent=0, indent_string='  '):
        return '$' + value


class Arg(BaseItem):
    'GraphQL :class:`Field` argument.'
    __slots__ = ('default',)

    def __init__(self, typ, graphql_name=None, default=None):
        '''
        :param typ: the :class:`Scalar` or :class:`Type` derived
          class. If this would cause a cross reference and the other
          type is not declared yet, then use the string name to query
          in the schema.
        :type typ: :class:`Scalar`, :class:`Type` or str

        :param graphql_name: the name to use in JSON object, usually ``aName``.
          If ``None`` or empty, will be created from python, converting
          ``a_name`` to ``aName`` using
          :func:`BaseItem._to_graphql_name()`
        :type graphql_name: str

        :param default: The default value for field. May be a value or
          :class:`Variable`.
        '''
        super(Arg, self).__init__(typ, graphql_name)
        self.default = default
        if default is not None:
            assert typ(default)

    def __to_graphql__(self, indent=0, indent_string='  '):
        default = ''
        if self.default is not None:
            default = self.type.__to_graphql_input__(
                self.default, indent, indent_string)
            default = ' = ' + default
        return '%s: %s%s' % (self.graphql_name, self.type, default)

    def __to_graphql_input__(self, value, indent=0, indent_string='  '):
        v = self.type.__to_graphql_input__(value, indent, indent_string)
        return '%s: %s' % (self.graphql_name, v)


class ArgDict(OrderedDict):
    '''The Field Argeters

    This takes care to ensure values are :class:`Arg`. For ease of
    use, can be created in various forms:

    >>> ArgDict(name=str)
    name: String

    >>> ArgDict({'name': str})
    name: String

    >>> ArgDict(('name', str), ('other', int))
    name: String, other: Int

    >>> ArgDict((('name', str), ('other', int)))
    name: String, other: Int
    '''
    def __init__(self, *lst, **mapping):
        super(ArgDict, self).__init__()

        if not lst and not mapping:
            return

        if len(lst) == 1:
            if lst[0] is None:
                lst = []
            elif isinstance(lst[0], (tuple, list)):
                lst = lst[0]
            elif isinstance(lst[0], dict):
                mapping.update(lst[0])
                lst = []

        for k, v in lst:
            if not isinstance(v, Arg):
                v = Arg(v)
            self[k] = v

        for k, v in mapping.items():
            if not isinstance(v, Arg):
                v = Arg(v)
            self[k] = v

    def _set_container(self, schema, container):
        for k, v in self.items():
            v._set_container(schema, container, k)

    def __to_graphql__(self, indent=0, indent_string='  '):
        n = len(self)
        if n == 0:
            return ''

        s = ['(']
        if n <= 3:
            args = (p.__to_graphql__(indent, indent_string)
                    for p in self.values())
            s.extend((', '.join(args), ')'))
        else:
            s.append('\n')
            prefix = indent_string * (indent + 1)
            for p in self.values():
                s.extend((prefix,
                          p.__to_graphql__(indent, indent_string),
                          '\n'))

            s.extend((indent_string * indent, ')'))
        return ''.join(s)

    def __to_graphql_input__(self, values, indent=0, indent_string='  '):
        n = len(values)
        if n == 0:
            return ''

        s = ['(']
        if n <= 3:
            args = []
            for k, v in values.items():
                p = self[k]
                args.append(p.__to_graphql_input__(v))
            s.extend((', '.join(args), ')'))
        else:
            s.append('\n')
            prefix = indent_string * (indent + 2)
            for k, v in values.items():
                p = self[k]
                s.extend((prefix,
                          p.__to_graphql_input__(v, indent, indent_string),
                          '\n'))

            s.extend((indent_string * (indent + 1), ')'))
        return ''.join(s)

    def __str__(self):
        return self.__to_graphql__()

    def __repr__(self):
        return self.__to_graphql__()

    def __bytes__(self):
        return bytes(self.__to_graphql__(indent_string=''), 'utf-8')


class Field(BaseItem):
    '''Field in a :class:`Type` container.

    Each field has a GraphQL type, such as a derived class from
    :class:`Scalar` or :class:`Type`, this is used for nesting,
    conversion to native Python types, generating queries, etc.
    '''

    __slots__ = ('args',)

    def __init__(self, typ, graphql_name=None, args=None):
        '''
        :param typ: the :class:`Scalar` or :class:`Type` derived
          class. If this would cause a cross reference and the other
          type is not declared yet, then use the string name to query
          in the schema.
        :type typ: :class:`Scalar`, :class:`Type` or str

        :param graphql_name: the name to use in JSON object, usually ``aName``.
          If ``None`` or empty, will be created from python, converting
          ``a_name`` to ``aName`` using
          :func:`BaseItem._to_graphql_name()`
        :type graphql_name: str

        :param args: The field parameters as a :class:`ArgDict` or
          compatible type (dict, or iterable of key-value pairs). The
          value may be a mapped Python type (ie: ``str``), explicit
          type (ie: ``String``), type name (ie: ``"String"``, to allow
          cross references) or :class:`Arg` instances.
        :type args: :class:`ArgDict`
        '''
        super(Field, self).__init__(typ, graphql_name)
        self.args = ArgDict(args)

    def _set_container(self, schema, container, name):
        super(Field, self)._set_container(schema, container, name)
        for k, v in self.args.items():
            v._set_container(schema, container, k)

    def __to_graphql__(self, indent=0, indent_string='  '):
        args = self.args.__to_graphql__(indent + 1, indent_string)
        return '%s%s: %s' % (self.graphql_name, args, self.type)

    def __repr__(self):
        return self.__to_graphql__()

    def __bytes__(self):
        return bytes(self.__to_graphql__(indent_string=''), 'utf-8')


class Type(ContainerType):
    '''GraphQL ``type Name``.

    If the subclass also adds :class:`Interface` to the class
    declarations, then it will emit ``type Name implements Iface1, Iface2``,
    also making their fields automatically available in the final
    class.
    '''
    __kind__ = 'type'


class Interface(ContainerType):
    '''GraphQL ``interface Name``.

    If the subclass also adds :class:`Interface` to the class
    declarations, then it will emit
    ``interface Name implements Iface1, Iface2``,
    also making their fields automatically available in the final
    class.
    '''
    __kind__ = 'interface'


class Input(ContainerType):
    'GraphQL ``input Name``.'
    __kind__ = 'input'

    @classmethod
    def __to_graphql_input__(cls, value, indent=0, indent_string='  '):
        args = []
        for k, v in value.items():
            f = cls[k]
            vs = f.type.__to_graphql_input__(v, indent, indent_string)
            args.append('%s: %s' % (f.graphql_name, vs))

        return '{' + ', '.join(args) + '}'


########################################################################
# Built-in types
########################################################################

class Int(Scalar):
    'Maps GraphQL ``Int`` to Python ``int``.'
    converter = int


class Float(Scalar):
    'Maps GraphQL ``Float`` to Python ``float``.'
    converter = float


class String(Scalar):
    'Maps GraphQL ``String`` to Python ``str``.'
    converter = str


class Boolean(Scalar):
    'Maps GraphQL ``Boolean`` to Python ``bool``.'
    converter = bool


class ID(Scalar):
    'Maps GraphQL ``ID`` to Python ``str``.'
    converter = str


map_python_to_graphql = {
    int: Int,
    float: Float,
    str: String,
    bool: Boolean,
    id: ID,
}
