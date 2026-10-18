import asyncio

from django.test import SimpleTestCase

from infrastructure.tenancy import TenantContext
from infrastructure.tenancy.middleware import TenantMiddleware


class TenantContextTests(SimpleTestCase):
    def tearDown(self):
        TenantContext.clear()

    def test_context_can_be_set_read_and_cleared(self):
        organization = object()

        TenantContext.set(organization)

        self.assertTrue(TenantContext.exists())
        self.assertIs(TenantContext.get(), organization)
        TenantContext.clear()
        self.assertFalse(TenantContext.exists())

    def test_middleware_clears_context_after_response_and_exception(self):
        organization = object()

        def response_with_tenant(request):
            TenantContext.set(organization)
            return 'response'

        middleware = TenantMiddleware(response_with_tenant)
        self.assertEqual(middleware(object()), 'response')
        self.assertFalse(TenantContext.exists())

        def failing_response(request):
            TenantContext.set(organization)
            raise RuntimeError('view failed')

        with self.assertRaises(RuntimeError):
            TenantMiddleware(failing_response)(object())
        self.assertFalse(TenantContext.exists())

    async def test_concurrent_requests_keep_tenant_contexts_isolated(self):
        async def response_with_tenant(organization):
            TenantContext.set(organization)
            await asyncio.sleep(0)
            return TenantContext.get()

        middleware = TenantMiddleware(response_with_tenant)
        first, second = await asyncio.gather(
            middleware('organization-a'),
            middleware('organization-b'),
        )

        self.assertEqual((first, second), ('organization-a', 'organization-b'))
        self.assertFalse(TenantContext.exists())