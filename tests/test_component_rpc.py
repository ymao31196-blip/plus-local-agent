"""Actual private Manager RPC dispatch checks; no GUI or MCP claims."""
from pathlib import Path
from desktop_runtime.manager import Manager

REPOSITORY = Path(__file__).resolve().parents[1] / 'test-provider-repository'


def test_rpc_repository_management(tmp_path):
    manager = Manager(tmp_path/'data', tmp_path/'resources')
    assert manager.dispatch('repository_list', {})['repositories'] == []
    url = REPOSITORY.as_uri()
    result = manager.dispatch('repository_add', {'url':url,'name':'Test','enabled':True})
    assert len(result['repositories']) == 1
    discovered = manager.dispatch('provider_discover', {'repository_url':url})
    assert any(row['provider_id']=='test-echo-provider' for row in discovered['providers'])
    details = manager.dispatch('provider_details_remote', {'provider_id':'test-echo-provider','repository_url':url})
    assert details['provider_id']=='test-echo-provider' and details['versions']
    assert manager.dispatch('repository_remove', {'url':url})['repositories']==[]


def test_rpc_installed_providers(tmp_path):
    manager=Manager(tmp_path/'data',tmp_path/'resources')
    assert manager.dispatch('provider_installed_list', {})['providers']==[]


def test_rpc_health_check(tmp_path):
    manager=Manager(tmp_path/'data',tmp_path/'resources')
    result=manager.dispatch('provider_health_check', {'provider_id':'missing-provider'})
    assert result['healthy'] is False and result['status']=='not_installed'


if __name__=='__main__':
    import pytest
    raise SystemExit(pytest.main([__file__,'-q']))
