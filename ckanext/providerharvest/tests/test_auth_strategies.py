import pytest

from ckanext.providerharvest.auth_strategies.api_key import ApiKeyAuth
from ckanext.providerharvest.secrets.base import SecretBundle


def test_api_key_header_default():
    kwargs = ApiKeyAuth().apply({}, SecretBundle(fields={"api_key": "abc123"}), {})
    assert kwargs["headers"] == {"X-API-Key": "abc123"}


def test_api_key_custom_header_name():
    kwargs = ApiKeyAuth().apply(
        {}, SecretBundle(fields={"api_key": "abc123"}), {"name": "Authorization"}
    )
    assert kwargs["headers"] == {"Authorization": "abc123"}


def test_api_key_query_param():
    kwargs = ApiKeyAuth().apply(
        {}, SecretBundle(fields={"api_key": "abc123"}), {"location": "query", "name": "token"}
    )
    assert kwargs["params"] == {"token": "abc123"}


def test_api_key_merges_with_existing_headers():
    kwargs = ApiKeyAuth().apply(
        {"headers": {"Accept": "application/json"}},
        SecretBundle(fields={"api_key": "abc123"}),
        {},
    )
    assert kwargs["headers"] == {"Accept": "application/json", "X-API-Key": "abc123"}


def test_api_key_missing_raises():
    with pytest.raises(ValueError):
        ApiKeyAuth().apply({}, SecretBundle(fields={}), {})


def test_api_key_does_not_mutate_input_kwargs():
    original = {"headers": {"Accept": "application/json"}}
    ApiKeyAuth().apply(original, SecretBundle(fields={"api_key": "abc123"}), {})
    assert original == {"headers": {"Accept": "application/json"}}
