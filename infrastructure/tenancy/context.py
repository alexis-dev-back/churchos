from contextvars import ContextVar, Token


_current_tenant = ContextVar('churchos_current_tenant', default=None)


class TenantContext:
    @classmethod
    def set(cls, organization):
        return _current_tenant.set(organization)

    @classmethod
    def get(cls):
        return _current_tenant.get()

    @classmethod
    def clear(cls):
        _current_tenant.set(None)

    @classmethod
    def reset(cls, token: Token):
        _current_tenant.reset(token)

    @classmethod
    def exists(cls):
        return cls.get() is not None