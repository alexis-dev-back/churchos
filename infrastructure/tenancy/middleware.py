from asgiref.sync import iscoroutinefunction, markcoroutinefunction

from .context import TenantContext


class TenantMiddleware:
    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.is_async = iscoroutinefunction(get_response)
        if self.is_async:
            markcoroutinefunction(self)

    def __call__(self, request):
        if self.is_async:
            return self.__acall__(request)

        token = TenantContext.set(None)
        try:
            return self.get_response(request)
        finally:
            TenantContext.reset(token)

    async def __acall__(self, request):
        token = TenantContext.set(None)
        try:
            return await self.get_response(request)
        finally:
            TenantContext.reset(token)