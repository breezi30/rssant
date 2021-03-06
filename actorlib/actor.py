import re
import os.path
import importlib
import inspect

from validr import T

from .helper import parse_actor_timer


def get_params(f, actor_name):
    sig = inspect.signature(f)
    params_schema = {}
    for name, p in list(sig.parameters.items())[1:]:
        if p.default is not inspect.Parameter.empty:
            raise ValueError(
                f'You should not set default in schema annotation in actor {actor_name}!')
        if p.annotation is inspect.Parameter.empty:
            raise ValueError(f'Missing annotation in parameter {name} in actor {actor_name}!')
        params_schema[name] = p.annotation
    if params_schema:
        return T.dict(params_schema).__schema__
    return None


def get_returns(f):
    sig = inspect.signature(f)
    if sig.return_annotation is not inspect.Signature.empty:
        schema = sig.return_annotation
        return T(schema).__schema__
    return None


class Actor:
    def __init__(self, handler, schema_compiler):
        self.name = handler.__actor_name__
        self.timer = handler.__actor_timer__
        self.is_async = self._is_async_handler(handler)
        self.module = self.get_module(self.name)
        self.handler = handler
        params_schema = get_params(handler, self.name)
        if params_schema:
            self._validate_params = schema_compiler.compile(params_schema)
        else:
            self._validate_params = None
        returns_schema = get_returns(handler)
        if returns_schema:
            self._validate_returns = schema_compiler.compile(returns_schema)
        else:
            self._validate_returns = None

    @staticmethod
    def _is_async_handler(handler):
        is_async = handler.__actor_is_async__
        if is_async is not None:
            return bool(is_async)
        if inspect.iscoroutinefunction(handler):
            return True
        handler_call = getattr(handler, '__call__', None)
        if handler_call and inspect.iscoroutinefunction(handler_call):
            return True
        return False

    @staticmethod
    def get_module(name):
        return name.split('.', maxsplit=1)[0]

    def __repr__(self):
        is_async = 'async ' if self.is_async else ''
        return '<{} {}{}>'.format(type(self).__name__, is_async, self.name)

    async def _async_handler(self, ctx, **params):
        ret = await self.handler(ctx, **params)
        return self._validate_returns(ret)

    def _thread_handler(self, ctx, **params):
        ret = self.handler(ctx, **params)
        return self._validate_returns(ret)

    def __call__(self, ctx):
        if self._validate_params is None:
            params = {}
        else:
            params = self._validate_params(ctx.message.content)
        if self._validate_returns is None:
            return self.handler(ctx, **params)
        if self.is_async:
            return self._async_handler(ctx, **params)
        else:
            return self._thread_handler(ctx, **params)


def actor(name, is_async=None, timer=None):

    if timer is not None:
        timer = parse_actor_timer(timer)

    def decorator(f):
        f.__actor_name__ = name
        f.__actor_timer__ = timer
        f.__actor_is_async__ = is_async
        return f

    return decorator


def import_all_modules(import_name):
    root = importlib.import_module(import_name)
    yield root
    if import_name == "__main__":
        return
    for root_path in set(getattr(root, "__path__", [])):
        root_path = root_path.rstrip("/")
        for root, dirs, files in os.walk(root_path):
            root = root.rstrip("/")
            if "__init__.py" in files:
                module = root[len(root_path):].replace("/", ".")
                if module:
                    module = f"{import_name}{module}"
                else:
                    module = import_name
                yield importlib.import_module(module)
            for filename in files:
                if filename not in ("__init__.py", "__main__.py") and filename.endswith(".py"):
                    module = os.path.splitext(os.path.join(root, filename))[0]
                    module = module[len(root_path):].replace("/", ".")
                    yield importlib.import_module(f"{import_name}{module}")


def import_all_actors(import_name, pattern=".*"):
    visited = set()
    pattern = re.compile(pattern)
    for module in import_all_modules(import_name):
        for obj in vars(module).values():
            if not hasattr(obj, '__actor_name__'):
                continue
            is_func = inspect.iscoroutinefunction(obj) or inspect.isfunction(obj)
            if not (is_func or inspect.isclass(obj)):
                continue
            if obj in visited:
                continue
            if pattern.fullmatch(obj.__name__):
                visited.add(obj)
                yield obj


def collect_actors(*modules):
    actors = set()
    for import_name in modules:
        for handler in import_all_actors(import_name):
            if handler in actors:
                continue
            actors.add(handler)
    return actors
