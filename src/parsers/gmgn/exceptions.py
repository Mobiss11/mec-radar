class GmgnError(Exception):
    pass


class CloudflareBlockedError(GmgnError):
    pass


class GmgnRateLimitError(GmgnError):
    pass


class GmgnApiError(GmgnError):
    pass
