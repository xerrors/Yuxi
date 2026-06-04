from __future__ import annotations
from yuxi.services.mcp_auth.fetchers.base import ITokenFetcher
from yuxi.services.mcp_auth.fetchers.http_fetcher import CustomHttpTokenFetcher, ClientCredentialsFetcher
from yuxi.services.mcp_auth.fetchers.oauth_fetcher import AuthorizationCodeFetcher


class TokenFetcherFactory:
    """TokenFetcher 工厂"""

    @staticmethod
    def get_fetcher(provider: str) -> ITokenFetcher:
        if provider == "custom_http_token":
            return CustomHttpTokenFetcher()
        elif provider == "client_credentials":
            return ClientCredentialsFetcher()
        elif provider == "authorization_code":
            return AuthorizationCodeFetcher()
        else:
            raise ValueError(f"Unsupported MCP auth provider for dynamic token: {provider}")
