# Copyright 2013 by Rackspace Hosting, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Hook decorators."""

from __future__ import annotations

from functools import wraps
from inspect import getmembers
from inspect import iscoroutinefunction
import re
import typing as t

from falcon.constants import COMBINED_METHODS
from falcon.util.misc import get_argnames
from falcon.util.sync import _wrap_non_coroutine_unsafe

if t.TYPE_CHECKING:  # pragma: no cover
    import falcon as wsgi
    from falcon import asgi

_DECORABLE_METHOD_NAME = re.compile(
    r'^on_({})(_\w+)?$'.format('|'.join(method.lower() for method in COMBINED_METHODS))
)

Resource = object
Responder = t.Callable
ResponderOrResource = t.Union[Responder, Resource]
Action = t.Callable


def before(
    action: Action, *args: t.Any, is_async: bool = False, **kwargs: t.Any
) -> t.Callable[[ResponderOrResource], ResponderOrResource]:
    """Execute the given action function *before* the responder.

    The `params` argument that is passed to the hook
    contains only the fields from the URI template path; it does not
    include query string values.

    Hooks may inject extra params as needed. For example::

        def do_something(req, resp, resource, params):
            try:
                params['id'] = int(params['id'])
            except ValueError:
                raise falcon.HTTPBadRequest(title='Invalid ID',
                                            description='ID was not valid.')

            params['answer'] = 42

    Args:
        action (callable): A function of the form
            ``func(req, resp, resource, params)``, where `resource` is a
            reference to the resource class instance associated with the
            request and `params` is a dict of URI template field names,
            if any, that will be passed into the resource responder as
            kwargs.

        *args: Any additional arguments will be passed to *action* in the
            order given, immediately following the *req*, *resp*, *resource*,
            and *params* arguments.

    Keyword Args:
        is_async (bool): Set to ``True`` for ASGI apps to provide a hint that
            the decorated responder is a coroutine function (i.e., that it
            is defined with ``async def``) or that it returns an awaitable
            coroutine object.

            Normally, when the function source is declared using ``async def``,
            the resulting function object is flagged to indicate it returns a
            coroutine when invoked, and this can be automatically detected.
            However, it is possible to use a regular function to return an
            awaitable coroutine object, in which case a hint is required to let
            the framework know what to expect. Also, a hint is always required
            when using a cythonized coroutine function, since Cython does not
            flag them in a way that can be detected in advance, even when the
            function is declared using ``async def``.

        **kwargs: Any additional keyword arguments will be passed through to
            *action*.
    """

    def _before(responder_or_resource: ResponderOrResource) -> ResponderOrResource:
        if isinstance(responder_or_resource, type):
            resource = responder_or_resource

            for responder_name, responder in getmembers(resource, callable):
                if _DECORABLE_METHOD_NAME.match(responder_name):
                    # This pattern is necessary to capture the current value of
                    # responder in the do_before_all closure; otherwise, they
                    # will capture the same responder variable that is shared
                    # between iterations of the for loop, above.
                    responder = t.cast(Responder, responder)

                    def let(responder: Responder = responder) -> None:
                        do_before_all = _wrap_with_before(
                            responder, action, args, kwargs, is_async
                        )

                        setattr(resource, responder_name, do_before_all)

                    let()

            return resource

        else:
            responder = t.cast(Responder, responder_or_resource)
            do_before_one = _wrap_with_before(responder, action, args, kwargs, is_async)

            return do_before_one

    return _before


def after(
    action: Action, *args: t.Any, is_async: bool = False, **kwargs: t.Any
) -> t.Callable[[ResponderOrResource], ResponderOrResource]:
    """Execute the given action function *after* the responder.

    Args:
        action (callable): A function of the form
            ``func(req, resp, resource)``, where `resource` is a
            reference to the resource class instance associated with the
            request

        *args: Any additional arguments will be passed to *action* in the
            order given, immediately following the *req*, *resp* and *resource*
            arguments.

    Keyword Args:
        is_async (bool): Set to ``True`` for ASGI apps to provide a hint that
            the decorated responder is a coroutine function (i.e., that it
            is defined with ``async def``) or that it returns an awaitable
            coroutine object.

            Normally, when the function source is declared using ``async def``,
            the resulting function object is flagged to indicate it returns a
            coroutine when invoked, and this can be automatically detected.
            However, it is possible to use a regular function to return an
            awaitable coroutine object, in which case a hint is required to let
            the framework know what to expect. Also, a hint is always required
            when using a cythonized coroutine function, since Cython does not
            flag them in a way that can be detected in advance, even when the
            function is declared using ``async def``.

        **kwargs: Any additional keyword arguments will be passed through to
            *action*.
    """

    def _after(responder_or_resource: ResponderOrResource) -> ResponderOrResource:
        if isinstance(responder_or_resource, type):
            resource = t.cast(Resource, responder_or_resource)

            for responder_name, responder in getmembers(resource, callable):
                if _DECORABLE_METHOD_NAME.match(responder_name):
                    responder = t.cast(Responder, responder)

                    def let(responder: Responder = responder) -> None:
                        do_after_all = _wrap_with_after(
                            responder, action, args, kwargs, is_async
                        )

                        setattr(resource, responder_name, do_after_all)

                    let()

            return resource

        else:
            responder = t.cast(Responder, responder_or_resource)
            do_after_one = _wrap_with_after(responder, action, args, kwargs, is_async)

            return do_after_one

    return _after


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _wrap_with_after(
    responder: Responder,
    action: Action,
    action_args: t.Any,
    action_kwargs: t.Any,
    is_async: bool,
) -> Responder:
    """Execute the given action function after a responder method.

    Args:
        responder: The responder method to wrap.
        action: A function with a signature similar to a resource responder
            method, taking the form ``func(req, resp, resource)``.
        action_args: Additional positional arguments to pass to *action*.
        action_kwargs: Additional keyword arguments to pass to *action*.
        is_async: Set to ``True`` for cythonized responders that are
            actually coroutine functions, since such responders can not
            be auto-detected. A hint is also required for regular functions
            that happen to return an awaitable coroutine object.
    """

    responder_argnames = get_argnames(responder)
    extra_argnames = responder_argnames[2:]  # Skip req, resp

    if is_async or iscoroutinefunction(responder):
        # NOTE(kgriffs): I manually verified that the implicit "else" branch
        #   is actually covered, but coverage isn't tracking it for
        #   some reason.
        if not is_async:  # pragma: nocover
            async_action = _wrap_non_coroutine_unsafe(action)
        else:
            async_action = action

        @wraps(responder)
        async def do_after(
            self: ResponderOrResource,
            req: asgi.Request,
            resp: asgi.Response,
            *args: t.Any,
            **kwargs: t.Any,
        ) -> None:
            if args:
                _merge_responder_args(args, kwargs, extra_argnames)

            await responder(self, req, resp, **kwargs)
            assert async_action
            await async_action(req, resp, self, *action_args, **action_kwargs)

    else:

        @wraps(responder)
        def do_after(
            self: ResponderOrResource,
            req: wsgi.Request,
            resp: wsgi.Response,
            *args: t.Any,
            **kwargs: t.Any,
        ) -> None:
            if args:
                _merge_responder_args(args, kwargs, extra_argnames)

            responder(self, req, resp, **kwargs)
            action(req, resp, self, *action_args, **action_kwargs)

    return do_after


def _wrap_with_before(
    responder: Responder,
    action: Action,
    action_args: t.Tuple[t.Any, ...],
    action_kwargs: t.Dict[str, t.Any],
    is_async: bool,
) -> t.Union[t.Callable[..., t.Awaitable[None]], t.Callable[..., None]]:
    """Execute the given action function before a responder method.

    Args:
        responder: The responder method to wrap.
        action: A function with a similar signature to a resource responder
            method, taking the form ``func(req, resp, resource, params)``.
        action_args: Additional positional arguments to pass to *action*.
        action_kwargs: Additional keyword arguments to pass to *action*.
        is_async: Set to ``True`` for cythonized responders that are
            actually coroutine functions, since such responders can not
            be auto-detected. A hint is also required for regular functions
            that happen to return an awaitable coroutine object.
    """

    responder_argnames = get_argnames(responder)
    extra_argnames = responder_argnames[2:]  # Skip req, resp

    if is_async or iscoroutinefunction(responder):
        # NOTE(kgriffs): I manually verified that the implicit "else" branch
        #   is actually covered, but coverage isn't tracking it for
        #   some reason.
        if not is_async:  # pragma: nocover
            async_action = _wrap_non_coroutine_unsafe(action)
        else:
            async_action = action

        @wraps(responder)
        async def do_before(
            self: ResponderOrResource,
            req: asgi.Request,
            resp: asgi.Response,
            *args: t.Any,
            **kwargs: t.Any,
        ) -> None:
            if args:
                _merge_responder_args(args, kwargs, extra_argnames)

            assert async_action
            await async_action(req, resp, self, kwargs, *action_args, **action_kwargs)
            await responder(self, req, resp, **kwargs)

    else:

        @wraps(responder)
        def do_before(
            self: ResponderOrResource,
            req: wsgi.Request,
            resp: wsgi.Response,
            *args: t.Any,
            **kwargs: t.Any,
        ) -> None:
            if args:
                _merge_responder_args(args, kwargs, extra_argnames)

            action(req, resp, self, kwargs, *action_args, **action_kwargs)
            responder(self, req, resp, **kwargs)

    return do_before


def _merge_responder_args(
    args: t.Tuple[t.Any, ...], kwargs: t.Dict[str, t.Any], argnames: t.List[str]
) -> None:
    """Merge responder args into kwargs.

    The framework always passes extra args as keyword arguments.
    However, when the app calls the responder directly, it might use
    positional arguments instead, so we need to handle that case. This
    might happen, for example, when overriding a resource and calling
    a responder via super().

    Args:
        args (tuple): Extra args passed into the responder
        kwargs (dict): Keyword args passed into the responder
        argnames (list): Extra argnames from the responder's
            signature, ordered as defined
    """

    # NOTE(kgriffs): Merge positional args into kwargs by matching
    # them up to the responder's signature. To do that, we must
    # find out the names of the positional arguments by matching
    # them in the order of the arguments named in the responder's
    # signature.
    for i, argname in enumerate(argnames):
        # NOTE(kgriffs): extra_argnames may contain keyword arguments,
        # which won't be in the args list, and are already in the kwargs
        # dict anyway, so detect and skip them.
        if argname not in kwargs:
            kwargs[argname] = args[i]
